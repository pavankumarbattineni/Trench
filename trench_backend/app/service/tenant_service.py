"""Business logic for tenants, membership, and roles.

A user's tenant and role live directly on their own row (`User.tenant_id`
+ `User.role`) -- there is no membership join table, so "the caller's
tenant, and their role in it" is always a single join-free read, and
"who's in this tenant" is a plain filter on `users.tenant_id`.

Ownership is `role == "owner"` scoped to the tenant. Exactly one Owner
exists per tenant (its creator); nobody, including other admins, can
change, demote, or remove them -- enforced here (`require_not_owner`),
deliberately not by any DB constraint.
"""

import uuid

from fastapi import HTTPException, status
from sqlalchemy import case, func, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.database.models import Tenant, User
from app.utils.email_domain import extract_domain, is_public_email_domain

OWNER = "owner"
ADMIN = "admin"
MEMBER = "member"
ADMIN_ROLES = (OWNER, ADMIN)

_ALREADY_IN_TENANT = HTTPException(
    status.HTTP_409_CONFLICT, "You already belong to a tenant"
)
_NAME_TAKEN = HTTPException(
    status.HTTP_409_CONFLICT, "A tenant with that name already exists"
)
_PUBLIC_DOMAIN = HTTPException(
    status.HTTP_422_UNPROCESSABLE_CONTENT,
    "Tenants can't be created with a personal email address (e.g. "
    "Gmail, Yahoo, Outlook). Sign up with your work email to create one.",
)
_DOMAIN_TAKEN = HTTPException(
    status.HTTP_409_CONFLICT,
    "A tenant already exists for your email domain -- ask its admin to "
    "add you as a member instead",
)
_NOT_FOUND = HTTPException(status.HTTP_404_NOT_FOUND, "Tenant not found")
_NOT_ADMIN = HTTPException(
    status.HTTP_403_FORBIDDEN, "Only a tenant admin can do that"
)
_NOT_OWNER = HTTPException(
    status.HTTP_403_FORBIDDEN, "Only the tenant owner can do that"
)
_NOT_A_MEMBER = HTTPException(
    status.HTTP_403_FORBIDDEN, "You don't belong to this tenant"
)
_MEMBER_NOT_FOUND = HTTPException(status.HTTP_404_NOT_FOUND, "Member not found")
_CANNOT_REMOVE_SELF = HTTPException(
    status.HTTP_409_CONFLICT,
    "An admin can't remove themself -- transfer admin to another member first",
)
_OWNER_PROTECTED = HTTPException(
    status.HTTP_403_FORBIDDEN,
    "The tenant owner's role and membership can't be changed by anyone",
)


class TenantService:
    @classmethod
    async def validate_domain_eligible_for_tenant(
        cls, db: AsyncSession, email: str
    ) -> str:
        """Returns the extracted domain if it's eligible to anchor a new
        tenant, raising the same errors `create` would.

        Callers (AuthService.signup_owner) use this to validate BEFORE
        creating the user row that will own the tenant -- otherwise a
        domain-ineligible signup attempt would still commit a User with no
        tenant. `create` re-checks the same conditions itself (cheap, and
        the only defense against a TOCTOU race between this check and the
        user actually being created).

        Raises:
            HTTPException: 422 if the domain is a public/personal
                provider; 409 if a tenant already exists for it.
        """
        domain = extract_domain(email)
        if is_public_email_domain(domain):
            raise _PUBLIC_DOMAIN
        if await cls.get_by_domain(db, domain) is not None:
            raise _DOMAIN_TAKEN
        return domain

    @staticmethod
    async def create(db: AsyncSession, *, creator: User, name: str) -> Tenant:
        """Creates a tenant with `creator` as its permanent Owner -- sets
        `creator.tenant_id` and `creator.role = "owner"` directly.

        Invitation-only from here on: no existing user is ever auto-added
        by matching domain (that was the pre-RBAC self-service model --
        see docs/superpowers/specs/2026-10-01-multi-tenant-rbac-design.md).
        Employees join only via InvitationService.accept.

        Raises:
            HTTPException: 409 if the caller already belongs to a tenant,
                or a tenant already exists for their email domain (or with
                that name); 422 if their email is a public/personal
                provider (Gmail, Yahoo, etc.) rather than a work domain.
        """
        if creator.tenant_id is not None:
            raise _ALREADY_IN_TENANT

        domain = extract_domain(creator.email)
        if is_public_email_domain(domain):
            raise _PUBLIC_DOMAIN
        if await TenantService.get_by_domain(db, domain) is not None:
            raise _DOMAIN_TAKEN

        tenant = Tenant(name=name, domain=domain)
        db.add(tenant)
        try:
            await db.flush()
        except IntegrityError as exc:
            await db.rollback()
            raise _NAME_TAKEN from exc

        creator.tenant_id = tenant.id
        creator.role = OWNER

        await db.commit()
        await db.refresh(tenant)
        return tenant

    @staticmethod
    async def get_by_id(db: AsyncSession, tenant_id: uuid.UUID) -> Tenant | None:
        result = await db.execute(select(Tenant).where(Tenant.id == tenant_id))
        return result.scalar_one_or_none()

    @staticmethod
    async def get_by_domain(db: AsyncSession, domain: str) -> Tenant | None:
        result = await db.execute(select(Tenant).where(Tenant.domain == domain))
        return result.scalar_one_or_none()

    @staticmethod
    def is_owner_of(user: User, tenant_id: uuid.UUID) -> bool:
        return user.tenant_id == tenant_id and user.role == OWNER

    @staticmethod
    async def get_owned_tenant(db: AsyncSession, user: User) -> Tenant | None:
        """Returns the tenant `user` is the permanent Owner of, or None.
        Used to block account deletion for an Owner (see
        AuthService.delete_account) -- deleting the Owner would leave the
        tenant with nobody able to perform Owner-only actions (promoting
        admins, setting the shared BYOK credential), which a plain "delete
        my account" action must never do unannounced."""
        if user.role != OWNER or user.tenant_id is None:
            return None
        return await TenantService.get_by_id(db, user.tenant_id)

    @classmethod
    async def require_admin_or_owner(
        cls, db: AsyncSession, *, user: User, tenant_id: uuid.UUID
    ) -> User:
        """Returns `user` iff they're an Admin or the Owner of
        `tenant_id`; raises 403/404 otherwise."""
        if await cls.get_by_id(db, tenant_id) is None:
            raise _NOT_FOUND
        if user.tenant_id != tenant_id or user.role not in ADMIN_ROLES:
            raise _NOT_ADMIN
        return user

    @classmethod
    async def require_owner(
        cls, db: AsyncSession, *, user: User, tenant_id: uuid.UUID
    ) -> User:
        """Returns `user` iff they're the Owner of `tenant_id`; raises
        403/404 otherwise. Used for Owner-only actions: promoting a Member
        to Admin, removing an Admin, setting the tenant's shared BYOK
        credential."""
        if await cls.get_by_id(db, tenant_id) is None:
            raise _NOT_FOUND
        if not cls.is_owner_of(user, tenant_id):
            raise _NOT_OWNER
        return user

    @classmethod
    async def require_member(
        cls, db: AsyncSession, *, user: User, tenant_id: uuid.UUID
    ) -> User:
        """Returns `user` iff they belong to `tenant_id`, admin or not --
        used for read-only endpoints (e.g. viewing the member roster) that
        any member should be able to reach, unlike mutations, which stay
        admin-only.

        Raises:
            HTTPException: 404 if the tenant doesn't exist; 403 if the
                caller doesn't belong to it.
        """
        if await cls.get_by_id(db, tenant_id) is None:
            raise _NOT_FOUND
        if user.tenant_id != tenant_id:
            raise _NOT_A_MEMBER
        return user

    @staticmethod
    async def list_members(
        db: AsyncSession,
        tenant_id: uuid.UUID,
        *,
        page: int = 1,
        page_size: int = 10,
        search: str | None = None,
    ) -> tuple[list[User], int]:
        """Returns (this page's members, total matching count).

        `search`, when given, matches a case-insensitive substring against
        either the member's username or email. Ordered owner first, then
        other admins, then regular members (each group then by account
        creation date) -- not just insertion order.
        """
        condition = User.tenant_id == tenant_id
        if search:
            pattern = f"%{search}%"
            condition = condition & (
                User.username.ilike(pattern) | User.email.ilike(pattern)
            )

        total = (
            await db.execute(select(func.count()).select_from(User).where(condition))
        ).scalar_one()

        rank = case((User.role == OWNER, 0), (User.role == ADMIN, 1), else_=2)
        result = await db.execute(
            select(User)
            .where(condition)
            .order_by(rank, User.created_at.asc())
            .offset((page - 1) * page_size)
            .limit(page_size)
        )
        return list(result.scalars().all()), total

    @staticmethod
    async def get_member(
        db: AsyncSession, *, tenant_id: uuid.UUID, user_id: uuid.UUID
    ) -> User | None:
        result = await db.execute(
            select(User).where(User.id == user_id, User.tenant_id == tenant_id)
        )
        return result.scalar_one_or_none()

    @classmethod
    async def update_role(
        cls,
        db: AsyncSession,
        *,
        tenant_id: uuid.UUID,
        target_user_id: uuid.UUID,
        role: str,
        acting_user: User,
    ) -> User:
        """Raises:
        HTTPException: 404 if the member doesn't exist; 403 if
            `target_user_id` is the tenant's permanent owner, if `role` is
            "owner" (never settable this way), or if the caller isn't the
            Owner (only the Owner promotes/demotes).
        """
        if role == OWNER or not cls.is_owner_of(acting_user, tenant_id):
            raise _NOT_OWNER
        await cls.require_not_owner(
            db, tenant_id=tenant_id, target_user_id=target_user_id
        )
        member = await cls.get_member(db, tenant_id=tenant_id, user_id=target_user_id)
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
        tenant_id: uuid.UUID,
        target_user_id: uuid.UUID,
        acting_user: User,
    ) -> None:
        """Detaches a member from the tenant: clears their `tenant_id`,
        revokes any company-knowledge grant, and resets their role to
        "member" -- their account (and personal knowledge) survives.

        Raises:
            HTTPException: 409 if the caller is trying to remove themself;
                403 if `target_user_id` is the tenant's permanent owner,
                or if the caller is an Admin trying to remove another
                Admin (only the Owner may do that); 404 if the member
                doesn't exist.
        """
        if target_user_id == acting_user.id:
            raise _CANNOT_REMOVE_SELF
        await cls.require_not_owner(
            db, tenant_id=tenant_id, target_user_id=target_user_id
        )
        member = await cls.get_member(db, tenant_id=tenant_id, user_id=target_user_id)
        if member is None:
            raise _MEMBER_NOT_FOUND
        if member.role == ADMIN and not cls.is_owner_of(acting_user, tenant_id):
            raise _NOT_OWNER
        member.tenant_id = None
        member.role = MEMBER
        member.has_company_knowledge_access = False
        await db.commit()

    @classmethod
    async def remove_all_members(
        cls, db: AsyncSession, *, tenant_id: uuid.UUID, acting_user_id: uuid.UUID
    ) -> int:
        """Removes every member of the tenant except the permanent owner
        (who can never be removed) and the caller themself (removing
        yourself via a bulk action would leave you unable to confirm what
        just happened, same reasoning as the single-remove self-check).
        Same per-member effect as `remove_member`.

        Returns:
            How many members were removed.
        """
        if await cls.get_by_id(db, tenant_id) is None:
            raise _NOT_FOUND
        result = await db.execute(
            update(User)
            .where(
                User.tenant_id == tenant_id,
                User.role != OWNER,
                User.id != acting_user_id,
            )
            .values(tenant_id=None, role=MEMBER, has_company_knowledge_access=False)
            .returning(User.id)
            .execution_options(synchronize_session="fetch")
        )
        removed_user_ids = result.scalars().all()
        await db.commit()
        return len(removed_user_ids)

    @staticmethod
    async def require_not_owner(
        db: AsyncSession, *, tenant_id: uuid.UUID, target_user_id: uuid.UUID
    ) -> None:
        """Raises 403 if `target_user_id` is `tenant_id`'s permanent Owner.
        Used by every mutation that acts on another member (role change,
        removal, knowledge-access grant/revoke) -- the Owner's role and
        membership can't be changed by anyone."""
        result = await db.execute(
            select(User.id).where(
                User.id == target_user_id,
                User.tenant_id == tenant_id,
                User.role == OWNER,
            )
        )
        if result.scalar_one_or_none() is not None:
            raise _OWNER_PROTECTED
