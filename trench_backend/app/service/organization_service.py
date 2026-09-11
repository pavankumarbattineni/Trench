"""Business logic for organizations, membership, and roles.

A user belongs to at most one organization (enforced by a unique
constraint on OrganizationMember.user_id), so "the caller's organization"
is always a single unambiguous lookup.
"""

import uuid

from fastapi import HTTPException, status
from sqlalchemy import case, delete, func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.database.models import Organization, OrganizationMember, User
from app.utils.email_domain import extract_domain, is_public_email_domain

_ALREADY_IN_ORG = HTTPException(
    status.HTTP_409_CONFLICT, "You already belong to an organization"
)
_NAME_TAKEN = HTTPException(
    status.HTTP_409_CONFLICT, "An organization with that name already exists"
)
_PUBLIC_DOMAIN = HTTPException(
    status.HTTP_422_UNPROCESSABLE_CONTENT,
    "Organizations can't be created with a personal email address (e.g. "
    "Gmail, Yahoo, Outlook). Sign up with your work email to create one.",
)
_DOMAIN_TAKEN = HTTPException(
    status.HTTP_409_CONFLICT,
    "An organization already exists for your email domain -- ask its "
    "admin to add you as a member instead",
)
_NOT_FOUND = HTTPException(status.HTTP_404_NOT_FOUND, "Organization not found")
_NOT_ADMIN = HTTPException(
    status.HTTP_403_FORBIDDEN, "Only an organization admin can do that"
)
_NOT_A_MEMBER = HTTPException(
    status.HTTP_403_FORBIDDEN, "You don't belong to this organization"
)
_USER_NOT_FOUND = HTTPException(status.HTTP_404_NOT_FOUND, "User not found")
_ALREADY_MEMBER = HTTPException(
    status.HTTP_409_CONFLICT, "That user already belongs to an organization"
)
_WRONG_DOMAIN = HTTPException(
    status.HTTP_422_UNPROCESSABLE_CONTENT,
    "That user's email domain doesn't match this organization's domain",
)
_MEMBER_NOT_FOUND = HTTPException(status.HTTP_404_NOT_FOUND, "Member not found")
_CANNOT_REMOVE_SELF = HTTPException(
    status.HTTP_409_CONFLICT,
    "An admin can't remove themself -- transfer admin to another member first",
)
_OWNER_PROTECTED = HTTPException(
    status.HTTP_403_FORBIDDEN,
    "The organization owner's role and membership can't be changed by anyone",
)


class OrganizationService:
    @staticmethod
    async def create(
        db: AsyncSession, *, creator: User, name: str
    ) -> tuple[Organization, OrganizationMember, int]:
        """Creates an organization with `creator` as its first admin and
        permanent owner.

        The organization's domain is derived from the creator's own email
        (never user-entered) -- see app/utils/email_domain.py. Any
        existing Trench user whose email already matches that domain is
        automatically added as a plain member (no company-knowledge
        access, no admin role -- the owner grants those explicitly
        afterward), since they're provably part of the same company.

        Raises:
            HTTPException: 409 if the caller already belongs to an
                organization, or an organization already exists for their
                email domain; 422 if their email is a public/personal
                provider (Gmail, Yahoo, etc.) rather than a work domain.

        Returns:
            The organization, the creator's own membership row, and how
            many other existing users were auto-added as members.
        """
        existing_membership = await OrganizationService.get_membership_for_user(
            db, creator.id
        )
        if existing_membership is not None:
            raise _ALREADY_IN_ORG

        domain = extract_domain(creator.email)
        if is_public_email_domain(domain):
            raise _PUBLIC_DOMAIN
        if await OrganizationService.get_by_domain(db, domain) is not None:
            raise _DOMAIN_TAKEN

        organization = Organization(name=name, domain=domain, owner_user_id=creator.id)
        db.add(organization)
        try:
            await db.flush()
        except IntegrityError as exc:
            await db.rollback()
            raise _NAME_TAKEN from exc

        member = OrganizationMember(
            organization_id=organization.id, user_id=creator.id, role="admin"
        )
        db.add(member)

        auto_added = await OrganizationService._auto_add_domain_users(
            db, organization=organization, exclude_user_id=creator.id
        )

        await db.commit()
        await db.refresh(organization)
        await db.refresh(member)
        return organization, member, auto_added

    @staticmethod
    async def _auto_add_domain_users(
        db: AsyncSession, *, organization: Organization, exclude_user_id: uuid.UUID
    ) -> int:
        """Adds every existing user whose email domain matches, as a plain
        member with no company-knowledge access -- the owner explicitly
        grants roles/access afterward (see add_member for the same rule
        applied to one user at a time). Skips anyone who (oddly) already
        belongs to some other organization, since membership is 1:1.
        """
        escaped = organization.domain.replace("\\", "\\\\").replace(
            "%", "\\%"
        ).replace("_", "\\_")
        result = await db.execute(
            select(User).where(User.email.ilike(f"%@{escaped}", escape="\\"))
        )
        candidates = [
            user
            for user in result.scalars().all()
            if user.id != exclude_user_id
            and extract_domain(user.email) == organization.domain
        ]
        if not candidates:
            return 0

        existing_member_user_ids = {
            row[0]
            for row in (
                await db.execute(
                    select(OrganizationMember.user_id).where(
                        OrganizationMember.user_id.in_([u.id for u in candidates])
                    )
                )
            ).all()
        }

        added = 0
        for user in candidates:
            if user.id in existing_member_user_ids:
                continue
            db.add(
                OrganizationMember(
                    organization_id=organization.id, user_id=user.id, role="member"
                )
            )
            added += 1
        return added

    @staticmethod
    async def get_membership_for_user(
        db: AsyncSession, user_id: uuid.UUID
    ) -> OrganizationMember | None:
        result = await db.execute(
            select(OrganizationMember).where(OrganizationMember.user_id == user_id)
        )
        return result.scalar_one_or_none()

    @staticmethod
    async def get_by_id(
        db: AsyncSession, organization_id: uuid.UUID
    ) -> Organization | None:
        result = await db.execute(
            select(Organization).where(Organization.id == organization_id)
        )
        return result.scalar_one_or_none()

    @staticmethod
    async def get_by_domain(db: AsyncSession, domain: str) -> Organization | None:
        result = await db.execute(
            select(Organization).where(Organization.domain == domain)
        )
        return result.scalar_one_or_none()

    @classmethod
    async def require_admin(
        cls, db: AsyncSession, *, user: User, organization_id: uuid.UUID
    ) -> OrganizationMember:
        """Returns the caller's membership row iff they're an admin of
        `organization_id`; raises 403/404 otherwise."""
        organization = await cls.get_by_id(db, organization_id)
        if organization is None:
            raise _NOT_FOUND

        membership = await cls.get_membership_for_user(db, user.id)
        if (
            membership is None
            or membership.organization_id != organization_id
            or membership.role != "admin"
        ):
            raise _NOT_ADMIN
        return membership

    @classmethod
    async def require_member(
        cls, db: AsyncSession, *, user: User, organization_id: uuid.UUID
    ) -> OrganizationMember:
        """Returns the caller's membership row iff they belong to
        `organization_id`, admin or not -- used for read-only endpoints
        (e.g. viewing the member roster) that any member should be able
        to reach, unlike mutations, which stay admin-only.

        Raises:
            HTTPException: 404 if the organization doesn't exist; 403 if
                the caller doesn't belong to it.
        """
        organization = await cls.get_by_id(db, organization_id)
        if organization is None:
            raise _NOT_FOUND

        membership = await cls.get_membership_for_user(db, user.id)
        if membership is None or membership.organization_id != organization_id:
            raise _NOT_A_MEMBER
        return membership

    @staticmethod
    async def list_members(
        db: AsyncSession,
        organization_id: uuid.UUID,
        *,
        page: int = 1,
        page_size: int = 10,
        search: str | None = None,
    ) -> tuple[list[OrganizationMember], int]:
        """Returns (this page's members, total matching count).

        `search`, when given, matches a case-insensitive substring against
        either the member's username or email (joined in from `users`,
        since neither lives on `OrganizationMember` itself). Ordered
        owner first, then other admins, then regular members (each group
        then by join date) -- not just insertion order.
        """
        base_query = select(OrganizationMember).join(
            User, User.id == OrganizationMember.user_id
        )
        count_query = (
            select(func.count())
            .select_from(OrganizationMember)
            .join(User, User.id == OrganizationMember.user_id)
        )
        condition = OrganizationMember.organization_id == organization_id
        if search:
            pattern = f"%{search}%"
            condition = condition & (
                User.username.ilike(pattern) | User.email.ilike(pattern)
            )
        base_query = base_query.where(condition)
        count_query = count_query.where(condition)

        total = (await db.execute(count_query)).scalar_one()

        organization = await OrganizationService.get_by_id(db, organization_id)
        owner_user_id = organization.owner_user_id if organization else None
        rank = case(
            (OrganizationMember.user_id == owner_user_id, 0),
            (OrganizationMember.role == "admin", 1),
            else_=2,
        )
        result = await db.execute(
            base_query.order_by(rank, OrganizationMember.created_at.asc())
            .offset((page - 1) * page_size)
            .limit(page_size)
        )
        return list(result.scalars().all()), total

    @staticmethod
    async def get_member(
        db: AsyncSession, *, organization_id: uuid.UUID, user_id: uuid.UUID
    ) -> OrganizationMember | None:
        result = await db.execute(
            select(OrganizationMember).where(
                OrganizationMember.organization_id == organization_id,
                OrganizationMember.user_id == user_id,
            )
        )
        return result.scalar_one_or_none()

    @staticmethod
    async def add_member(
        db: AsyncSession,
        *,
        organization: Organization,
        target_user: User,
        role: str = "member",
    ) -> OrganizationMember:
        """Raises:
        HTTPException: 409 if the target user already belongs to an
            organization (any organization, not just this one -- a user
            belongs to at most one); 422 if their email domain doesn't
            match this organization's domain.
        """
        existing = await OrganizationService.get_membership_for_user(
            db, target_user.id
        )
        if existing is not None:
            raise _ALREADY_MEMBER
        if extract_domain(target_user.email) != organization.domain:
            raise _WRONG_DOMAIN

        member = OrganizationMember(
            organization_id=organization.id, user_id=target_user.id, role=role
        )
        db.add(member)
        await db.commit()
        await db.refresh(member)
        return member

    @classmethod
    async def update_role(
        cls,
        db: AsyncSession,
        *,
        organization_id: uuid.UUID,
        target_user_id: uuid.UUID,
        role: str,
    ) -> OrganizationMember:
        """Raises:
        HTTPException: 404 if the member doesn't exist; 403 if
            `target_user_id` is the organization's permanent owner --
            their role can never be changed, by anyone, including other
            admins.
        """
        await cls._require_not_owner(db, organization_id, target_user_id)
        result = await db.execute(
            select(OrganizationMember).where(
                OrganizationMember.organization_id == organization_id,
                OrganizationMember.user_id == target_user_id,
            )
        )
        member = result.scalar_one_or_none()
        if member is None:
            raise _MEMBER_NOT_FOUND
        member.role = role
        await db.commit()
        await db.refresh(member)
        return member

    @classmethod
    async def remove_member(
        cls,
        db: AsyncSession,
        *,
        organization_id: uuid.UUID,
        target_user_id: uuid.UUID,
        acting_user_id: uuid.UUID,
    ) -> None:
        """Raises:
        HTTPException: 409 if the caller is trying to remove themself;
            403 if `target_user_id` is the organization's permanent
            owner -- they can never be removed, by anyone; 404 if the
            member doesn't exist.
        """
        if target_user_id == acting_user_id:
            raise _CANNOT_REMOVE_SELF
        await cls._require_not_owner(db, organization_id, target_user_id)
        result = await db.execute(
            select(OrganizationMember).where(
                OrganizationMember.organization_id == organization_id,
                OrganizationMember.user_id == target_user_id,
            )
        )
        member = result.scalar_one_or_none()
        if member is None:
            raise _MEMBER_NOT_FOUND
        await db.delete(member)
        await db.commit()

    @classmethod
    async def remove_all_members(
        cls, db: AsyncSession, *, organization_id: uuid.UUID, acting_user_id: uuid.UUID
    ) -> int:
        """Removes every member of the organization except the permanent
        owner (who can never be removed) and the caller themself (removing
        yourself via a bulk action would leave you unable to confirm what
        just happened, same reasoning as the single-remove self-check).

        Returns:
            How many members were removed.
        """
        organization = await cls.get_by_id(db, organization_id)
        if organization is None:
            raise _NOT_FOUND
        result = await db.execute(
            delete(OrganizationMember)
            .where(
                OrganizationMember.organization_id == organization_id,
                OrganizationMember.user_id != organization.owner_user_id,
                OrganizationMember.user_id != acting_user_id,
            )
            .returning(OrganizationMember.id)
        )
        removed_ids = result.scalars().all()
        await db.commit()
        return len(removed_ids)

    @classmethod
    async def require_not_owner(
        cls, db: AsyncSession, *, organization_id: uuid.UUID, target_user_id: uuid.UUID
    ) -> None:
        """Public entry point for callers outside this service (e.g. the
        knowledge-access grant/revoke endpoints) that need the same
        owner-protection rule applied to a user they're about to act on."""
        await cls._require_not_owner(db, organization_id, target_user_id)

    @staticmethod
    async def _require_not_owner(
        db: AsyncSession, organization_id: uuid.UUID, target_user_id: uuid.UUID
    ) -> None:
        organization = await OrganizationService.get_by_id(db, organization_id)
        if organization is not None and organization.owner_user_id == target_user_id:
            raise _OWNER_PROTECTED
