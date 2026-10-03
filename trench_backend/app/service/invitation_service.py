"""Business logic for tenant invitations: creating, resending,
revoking, and accepting them.

Only the raw token is ever emailed; only its sha256 hash is persisted
(see Invitation.token_hash) -- the same "secrets never stored in
plaintext" rule BYOK credentials follow (see app/utils/encryption.py),
just hashed rather than encrypted since a token is a lookup key, never
decrypted back.
"""

import hashlib
import secrets
import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.database.models import Invitation, Tenant, User
from app.service.user_service import UserService
from app.utils.email import send_email

INVITATION_EXPIRY = timedelta(days=7)


class TooManyRoleError(Exception):
    """Raised when an Admin (not the Owner) tries to invite someone as
    "admin" -- Admins may only invite Members."""


class DomainMismatchError(Exception):
    """Raised when the invited email's domain doesn't match the
    tenant's own domain -- a tenant can only invite members of its own
    company."""


def _hash_token(raw_token: str) -> str:
    return hashlib.sha256(raw_token.encode()).hexdigest()


def _invite_email_body(
    *, tenant_name: str, role: str, accept_url: str
) -> tuple[str, str]:
    text_body = (
        f"You've been invited to join {tenant_name} on Trench as a {role}.\n\n"
        f"Accept your invitation: {accept_url}\n\n"
        "This link expires in 7 days."
    )
    html_body = (
        f"<p>You've been invited to join <strong>{tenant_name}</strong> "
        f"on Trench as a {role}.</p>"
        f'<p><a href="{accept_url}">Accept your invitation</a></p>'
        "<p>This link expires in 7 days.</p>"
    )
    return html_body, text_body


class InvitationService:
    class InvalidTokenError(Exception):
        """Raised when a token is missing, revoked, expired, or already accepted."""

    class EmailMismatchError(Exception):
        """Raised when the authenticated email doesn't match the invited one."""

    class AlreadyInTenantError(Exception):
        """Raised when the invitee's existing account already belongs to a
        tenant -- a user belongs to at most one, and accepting must never
        silently move someone (possibly another tenant's Owner) out of
        theirs."""

    class EmailDeliveryError(Exception):
        """Raised when the invitation/resend email itself fails to send
        (e.g. the SMTP server is unreachable). The Invitation row is
        already committed by the time this can be raised -- deliberately
        not rolled back, since Resend (which reuses the same row) is
        exactly how the Owner/Admin recovers from a transient failure."""

    @staticmethod
    async def create(
        db: AsyncSession,
        *,
        tenant: Tenant,
        inviter: User,
        email: str,
        role: str,
    ) -> tuple[Invitation, str]:
        """Creates (or re-uses/rotates) a pending invitation and emails it.

        `inviter` must already be authorized as an Admin/Owner of `tenant`
        (the router's job) -- this only applies the role ceiling.

        Raises:
            TooManyRoleError: if `inviter.role == "admin"` and
                `role != "member"` -- Admins can only invite Members.
            DomainMismatchError: if `email`'s domain doesn't match the
                tenant's own domain.
        """
        if role not in ("admin", "member"):
            raise ValueError(f"Invalid invitation role: {role!r}")
        if inviter.role == "admin" and role != "member":
            raise TooManyRoleError("Admins may only invite members")
        email_domain = email.rsplit("@", 1)[-1].lower()
        if email_domain != tenant.domain.lower():
            raise DomainMismatchError(
                f"{email} doesn't match this tenant's domain ({tenant.domain})"
            )

        raw_token = secrets.token_urlsafe(32)
        token_hash = _hash_token(raw_token)

        result = await db.execute(
            select(Invitation).where(
                Invitation.tenant_id == tenant.id,
                Invitation.email == email,
                Invitation.status == "pending",
            )
        )
        invitation = result.scalar_one_or_none()
        if invitation is None:
            invitation = Invitation(
                tenant_id=tenant.id,
                email=email,
                role=role,
                invited_by_user_id=inviter.id,
                token_hash=token_hash,
                expires_at=datetime.now(UTC) + INVITATION_EXPIRY,
            )
            db.add(invitation)
        else:
            invitation.role = role
            invitation.token_hash = token_hash
            invitation.expires_at = datetime.now(UTC) + INVITATION_EXPIRY
            invitation.invited_by_user_id = inviter.id

        await db.commit()
        await db.refresh(invitation)

        frontend_base_url = get_settings().TRENCH_CONFIG.FRONTEND_BASE_URL
        accept_url = f"{frontend_base_url}/invite/accept?token={raw_token}"
        html_body, text_body = _invite_email_body(
            tenant_name=tenant.name, role=role, accept_url=accept_url
        )
        try:
            await send_email(
                to=email,
                subject=f"You're invited to join {tenant.name} on Trench",
                html_body=html_body,
                text_body=text_body,
            )
        except Exception as exc:
            raise InvitationService.EmailDeliveryError(
                "The invitation was created, but the email couldn't be "
                "sent. Use Resend to try again."
            ) from exc
        return invitation, raw_token

    @staticmethod
    async def resend(
        db: AsyncSession, *, invitation: Invitation, tenant: Tenant
    ) -> str:
        """Rotates the token and expiry on an existing pending invitation
        and re-sends the email. Returns the new raw token (for tests;
        production callers don't need it, the email already went out)."""
        raw_token = secrets.token_urlsafe(32)
        invitation.token_hash = _hash_token(raw_token)
        invitation.expires_at = datetime.now(UTC) + INVITATION_EXPIRY
        await db.commit()

        frontend_base_url = get_settings().TRENCH_CONFIG.FRONTEND_BASE_URL
        accept_url = f"{frontend_base_url}/invite/accept?token={raw_token}"
        html_body, text_body = _invite_email_body(
            tenant_name=tenant.name,
            role=invitation.role,
            accept_url=accept_url,
        )
        try:
            await send_email(
                to=invitation.email,
                subject=f"Reminder: you're invited to join {tenant.name} on Trench",
                html_body=html_body,
                text_body=text_body,
            )
        except Exception as exc:
            raise InvitationService.EmailDeliveryError(
                "The invitation link was refreshed, but the reminder email "
                "couldn't be sent. Try Resend again."
            ) from exc
        return raw_token

    @staticmethod
    async def revoke(db: AsyncSession, *, invitation: Invitation) -> None:
        invitation.status = "revoked"
        await db.commit()

    @staticmethod
    async def get_by_id(
        db: AsyncSession, invitation_id: uuid.UUID
    ) -> Invitation | None:
        return await db.get(Invitation, invitation_id)

    @staticmethod
    async def list_for_tenant(
        db: AsyncSession, tenant_id: uuid.UUID
    ) -> list[Invitation]:
        result = await db.execute(
            select(Invitation)
            .where(Invitation.tenant_id == tenant_id)
            .order_by(Invitation.created_at.desc())
        )
        return list(result.scalars().all())

    @classmethod
    async def accept(
        cls,
        db: AsyncSession,
        *,
        raw_token: str,
        authenticated_email: str,
        desired_username: str | None = None,
    ) -> tuple[Invitation, User, bool]:
        """Resolves a raw token, validates it, and either creates a new
        User or reuses an existing tenantless one (e.g. a member removed
        from this tenant earlier, now re-invited) -- then sets the user's
        `tenant_id` and `role` directly from the invitation and, for
        role="member", turns on their company-knowledge grant by default
        (Owner/Admin need no grant; their access is role-derived).

        Returns:
            (invitation, user, created) -- `created` is True iff a new
            User row was created by this call.

        Raises:
            InvalidTokenError: token not found, revoked, expired, or
                already accepted.
            EmailMismatchError: `authenticated_email` doesn't exactly
                match `invitation.email`.
            AlreadyInTenantError: the invitee already belongs to a tenant
                (a user belongs to at most one).
        """
        token_hash = _hash_token(raw_token)
        result = await db.execute(
            select(Invitation).where(Invitation.token_hash == token_hash)
        )
        invitation = result.scalar_one_or_none()
        if invitation is None or invitation.status != "pending":
            raise cls.InvalidTokenError("Invitation not found or no longer pending")
        if invitation.expires_at < datetime.now(UTC):
            invitation.status = "expired"
            await db.commit()
            raise cls.InvalidTokenError("Invitation has expired")

        if authenticated_email != invitation.email:
            raise cls.EmailMismatchError(
                f"This invite was sent to {invitation.email}; please sign in "
                "with that address"
            )

        grant_company_access = invitation.role == "member"
        user = await UserService.get_by_email(db, authenticated_email)
        created = False
        if user is None:
            user = await UserService.create_user(
                db,
                email=authenticated_email,
                desired_username=desired_username,
                tenant_id=invitation.tenant_id,
                role=invitation.role,
                has_company_knowledge_access=grant_company_access,
            )
            created = True
        elif user.tenant_id is not None:
            raise cls.AlreadyInTenantError(
                "This account already belongs to a tenant"
            )
        else:
            user.tenant_id = invitation.tenant_id
            user.role = invitation.role
            user.has_company_knowledge_access = grant_company_access

        invitation.status = "accepted"
        invitation.accepted_at = datetime.now(UTC)
        await db.commit()
        await db.refresh(user)
        return invitation, user, created
