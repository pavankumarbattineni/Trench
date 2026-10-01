"""Business logic for organizations, membership, and roles.

A user belongs to at most one organization (enforced by a unique
constraint on OrganizationMember.user_id), so "the caller's organization"
is always a single unambiguous lookup.
"""

import uuid

from fastapi import HTTPException, status
from sqlalchemy import case, delete, func, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.database.models import KnowledgeAccess, Organization, OrganizationMember, User
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
_NOT_OWNER = HTTPException(
    status.HTTP_403_FORBIDDEN, "Only the organization owner can do that"
)
_NOT_A_MEMBER = HTTPException(
    status.HTTP_403_FORBIDDEN, "You don't belong to this organization"
)
_USER_NOT_FOUND = HTTPException(status.HTTP_404_NOT_FOUND, "User not found")
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
    @classmethod
    async def validate_domain_eligible_for_org(
        cls, db: AsyncSession, email: str
    ) -> str:
        """Returns the extracted domain if it's eligible to anchor a new
        organization, raising the same errors `create` would.

        Callers (AuthService.signup_owner) use this to validate BEFORE
        creating the user row the organization will be owned by --
        otherwise a domain-ineligible signup attempt would still commit a
        User with no organization, violating "every user belongs to
        exactly one organization." `create` re-checks the same conditions
        itself (cheap, and the only defense against a TOCTOU race between
        this check and the user actually being created).

        Raises:
            HTTPException: 422 if the domain is a public/personal
                provider; 409 if an organization already exists for it.
        """
        domain = extract_domain(email)
        if is_public_email_domain(domain):
            raise _PUBLIC_DOMAIN
        if await cls.get_by_domain(db, domain) is not None:
            raise _DOMAIN_TAKEN
        return domain

    @staticmethod
    async def create(
        db: AsyncSession, *, creator: User, name: str
    ) -> tuple[Organization, OrganizationMember]:
        """Creates an organization with `creator` as its permanent Owner.

        Invitation-only from here on: no existing user is ever auto-added
        by matching domain (that was the pre-RBAC self-service model --
        see docs/superpowers/specs/2026-10-01-multi-tenant-rbac-design.md).
        Employees join only via InvitationService.accept.

        Raises:
            HTTPException: 409 if the caller already belongs to an
                organization, or an organization already exists for their
                email domain; 422 if their email is a public/personal
                provider (Gmail, Yahoo, etc.) rather than a work domain.

        Returns:
            The organization and the creator's own (role="owner")
            membership row.
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
            organization_id=organization.id, user_id=creator.id, role="owner"
        )
        db.add(member)

        creator.organization_id = organization.id

        await db.commit()
        await db.refresh(organization)
        await db.refresh(member)
        return organization, member

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

    @staticmethod
    async def get_owned_organization(
        db: AsyncSession, user_id: uuid.UUID
    ) -> Organization | None:
        """Returns the organization `user_id` is the permanent Owner of,
        or None. Used to block account deletion for an Owner (see
        AuthService.delete_account) -- Organization.owner_user_id has no
        cascading delete, and silently cascading it would destroy every
        other member's access to the organization's knowledge, which a
        plain "delete my account" action must never do unannounced."""
        result = await db.execute(
            select(Organization).where(Organization.owner_user_id == user_id)
        )
        return result.scalar_one_or_none()

    @classmethod
    async def require_admin_or_owner(
        cls, db: AsyncSession, *, user: User, organization_id: uuid.UUID
    ) -> OrganizationMember:
        """Returns the caller's membership row iff they're an Admin or the
        Owner of `organization_id`; raises 403/404 otherwise."""
        organization = await cls.get_by_id(db, organization_id)
        if organization is None:
            raise _NOT_FOUND

        membership = await cls.get_membership_for_user(db, user.id)
        if (
            membership is None
            or membership.organization_id != organization_id
            or membership.role not in ("admin", "owner")
        ):
            raise _NOT_ADMIN
        return membership

    @classmethod
    async def require_owner(
        cls, db: AsyncSession, *, user: User, organization_id: uuid.UUID
    ) -> OrganizationMember:
        """Returns the caller's membership row iff they're the Owner of
        `organization_id`; raises 403/404 otherwise. Used for Owner-only
        actions: promoting a Member to Admin, removing an Admin, setting
        the organization's shared BYOK credential."""
        organization = await cls.get_by_id(db, organization_id)
        if organization is None:
            raise _NOT_FOUND

        membership = await cls.get_membership_for_user(db, user.id)
        if (
            membership is None
            or membership.organization_id != organization_id
            or membership.role != "owner"
        ):
            raise _NOT_OWNER
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

    @classmethod
    async def update_role(
        cls,
        db: AsyncSession,
        *,
        organization_id: uuid.UUID,
        target_user_id: uuid.UUID,
        role: str,
        acting_membership: OrganizationMember,
    ) -> OrganizationMember:
        """Raises:
        HTTPException: 404 if the member doesn't exist; 403 if
            `target_user_id` is the organization's permanent owner, if
            `role` is "owner" (never settable this way), or if the caller
            isn't the Owner (only the Owner promotes/demotes).
        """
        if role == "owner" or acting_membership.role != "owner":
            raise _NOT_OWNER
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
        acting_membership: OrganizationMember,
    ) -> None:
        """Raises:
        HTTPException: 409 if the caller is trying to remove themself;
            403 if `target_user_id` is the organization's permanent
            owner, or if the caller is an Admin trying to remove another
            Admin (only the Owner may do that); 404 if the member doesn't
            exist.
        """
        if target_user_id == acting_membership.user_id:
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
        if member.role == "admin" and acting_membership.role != "owner":
            raise _NOT_OWNER
        await db.delete(member)
        await db.execute(
            delete(KnowledgeAccess).where(
                KnowledgeAccess.organization_id == organization_id,
                KnowledgeAccess.user_id == target_user_id,
            )
        )
        await db.execute(
            update(User)
            .where(User.id == target_user_id, User.organization_id == organization_id)
            .values(organization_id=None)
        )
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
            .returning(OrganizationMember.user_id)
        )
        removed_user_ids = result.scalars().all()
        if removed_user_ids:
            await db.execute(
                delete(KnowledgeAccess).where(
                    KnowledgeAccess.organization_id == organization_id,
                    KnowledgeAccess.user_id.in_(removed_user_ids),
                )
            )
            await db.execute(
                update(User)
                .where(
                    User.id.in_(removed_user_ids),
                    User.organization_id == organization_id,
                )
                .values(organization_id=None)
            )
        await db.commit()
        return len(removed_user_ids)

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
