"""Business logic for organization invitations: creating, resending,
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

from app.database.models import Invitation, Organization, OrganizationMember, User
from app.service.knowledge_access_service import KnowledgeAccessService
from app.service.user_service import UserService
from app.utils.email import send_email

INVITATION_EXPIRY = timedelta(days=7)


class TooManyRoleError(Exception):
    """Raised when an Admin (not the Owner) tries to invite someone as
    "admin" -- Admins may only invite Members."""


class DomainMismatchError(Exception):
    """Raised when the invited email's domain doesn't match the
    organization's own domain -- an org can only invite members of its
    own company."""


def _hash_token(raw_token: str) -> str:
    return hashlib.sha256(raw_token.encode()).hexdigest()


def _invite_email_body(
    *, organization_name: str, role: str, accept_url: str
) -> tuple[str, str]:
    text_body = (
        f"You've been invited to join {organization_name} on Trench as a {role}.\n\n"
        f"Accept your invitation: {accept_url}\n\n"
        "This link expires in 7 days."
    )
    html_body = (
        f"<p>You've been invited to join <strong>{organization_name}</strong> "
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
        organization: Organization,
        inviter_membership: OrganizationMember,
        email: str,
        role: str,
    ) -> tuple[Invitation, str]:
        """Creates (or re-uses/rotates) a pending invitation and emails it.

        Raises:
            TooManyRoleError: if `inviter_membership.role == "admin"` and
                `role != "member"` -- Admins can only invite Members.
            DomainMismatchError: if `email`'s domain doesn't match the
                organization's own domain.
        """
        if role not in ("admin", "member"):
            raise ValueError(f"Invalid invitation role: {role!r}")
        if inviter_membership.role == "admin" and role != "member":
            raise TooManyRoleError("Admins may only invite members")
        email_domain = email.rsplit("@", 1)[-1].lower()
        if email_domain != organization.domain.lower():
            raise DomainMismatchError(
                f"{email} doesn't match this organization's domain "
                f"({organization.domain})"
            )

        raw_token = secrets.token_urlsafe(32)
        token_hash = _hash_token(raw_token)

        result = await db.execute(
            select(Invitation).where(
                Invitation.organization_id == organization.id,
                Invitation.email == email,
                Invitation.status == "pending",
            )
        )
        invitation = result.scalar_one_or_none()
        if invitation is None:
            invitation = Invitation(
                organization_id=organization.id,
                email=email,
                role=role,
                invited_by_user_id=inviter_membership.user_id,
                token_hash=token_hash,
                expires_at=datetime.now(UTC) + INVITATION_EXPIRY,
            )
            db.add(invitation)
        else:
            invitation.role = role
            invitation.token_hash = token_hash
            invitation.expires_at = datetime.now(UTC) + INVITATION_EXPIRY
            invitation.invited_by_user_id = inviter_membership.user_id

        await db.commit()
        await db.refresh(invitation)

        accept_url = f"https://app.trench.example/invite/accept?token={raw_token}"
        html_body, text_body = _invite_email_body(
            organization_name=organization.name, role=role, accept_url=accept_url
        )
        try:
            await send_email(
                to=email,
                subject=f"You're invited to join {organization.name} on Trench",
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
        db: AsyncSession, *, invitation: Invitation, organization: Organization
    ) -> str:
        """Rotates the token and expiry on an existing pending invitation
        and re-sends the email. Returns the new raw token (for tests;
        production callers don't need it, the email already went out)."""
        raw_token = secrets.token_urlsafe(32)
        invitation.token_hash = _hash_token(raw_token)
        invitation.expires_at = datetime.now(UTC) + INVITATION_EXPIRY
        await db.commit()

        accept_url = f"https://app.trench.example/invite/accept?token={raw_token}"
        html_body, text_body = _invite_email_body(
            organization_name=organization.name,
            role=invitation.role,
            accept_url=accept_url,
        )
        try:
            await send_email(
                to=invitation.email,
                subject=(
                    f"Reminder: you're invited to join {organization.name} on Trench"
                ),
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
    async def list_for_organization(
        db: AsyncSession, organization_id: uuid.UUID
    ) -> list[Invitation]:
        result = await db.execute(
            select(Invitation)
            .where(Invitation.organization_id == organization_id)
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
        User or reuses an existing one (a user invited to a second
        organization isn't possible under the one-org-per-user rule, but
        re-accepting after a prior failed attempt mid-flow should still
        work) -- then creates the OrganizationMember row and, for
        role="member", an immediate default-on KnowledgeAccess grant
        (Owner/Admin need no grant row; their access is role-derived).

        Returns:
            (invitation, user, created) -- `created` is True iff a new
            User row was created by this call.

        Raises:
            InvalidTokenError: token not found, revoked, expired, or
                already accepted.
            EmailMismatchError: `authenticated_email` doesn't exactly
                match `invitation.email`.
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

        user = await UserService.get_by_email(db, authenticated_email)
        created = False
        if user is None:
            user = await UserService.create_user(
                db, email=authenticated_email, desired_username=desired_username
            )
            created = True

        user.organization_id = invitation.organization_id
        db.add(
            OrganizationMember(
                organization_id=invitation.organization_id,
                user_id=user.id,
                role=invitation.role,
            )
        )
        invitation.status = "accepted"
        invitation.accepted_at = datetime.now(UTC)
        await db.commit()
        await db.refresh(user)

        if invitation.role == "member":
            await KnowledgeAccessService.grant(
                db, organization_id=invitation.organization_id, user_id=user.id
            )

        return invitation, user, created
