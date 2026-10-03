"use client";

import { RotateCw, X } from "lucide-react";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import type { Invitation } from "@/lib/tenants";

interface InvitationRowProps {
  invitation: Invitation;
  onResend?: (invitation: Invitation) => void;
  onRevoke?: (invitation: Invitation) => void;
  resendPending?: boolean;
}

const ROLE_LABEL: Record<Invitation["role"], string> = {
  member: "Member",
  admin: "Admin",
};

function subtitleFor(invitation: Invitation): string {
  const roleLabel = ROLE_LABEL[invitation.role];
  const expiresDate = new Date(invitation.expires_at).toLocaleDateString();
  if (invitation.status === "accepted") {
    const acceptedDate = invitation.accepted_at
      ? new Date(invitation.accepted_at).toLocaleDateString()
      : null;
    return acceptedDate
      ? `Invited as ${roleLabel} · Accepted on ${acceptedDate}`
      : `Invited as ${roleLabel} · Accepted`;
  }
  if (invitation.status === "revoked") {
    return `Invited as ${roleLabel} · Declined (revoked)`;
  }
  if (invitation.status === "expired") {
    return `Invited as ${roleLabel} · Declined (expired ${expiresDate})`;
  }
  return `Invited as ${roleLabel} · Expires ${expiresDate}`;
}

/** "Declined" isn't a real backend status (there's no invitee-initiated
 * decline action yet) -- it's a UI label grouping "revoked" and "expired"
 * together, since both mean "this invite will never become a membership",
 * distinct from "pending". The row's own subtext always shows the real
 * underlying reason regardless of which section it's grouped into. */
export function InvitationRow({
  invitation,
  onResend,
  onRevoke,
  resendPending,
}: InvitationRowProps) {
  const isPending = invitation.status === "pending";
  return (
    <div className="flex items-center gap-3 rounded-xl border border-border bg-card px-4 py-3">
      <div className="min-w-0 flex-1">
        <p className="truncate text-sm font-medium">{invitation.email}</p>
        <p className="text-xs text-muted-foreground">{subtitleFor(invitation)}</p>
      </div>
      <Badge
        variant={
          invitation.status === "accepted"
            ? "default"
            : invitation.status === "pending"
              ? "outline"
              : "destructive"
        }
      >
        {invitation.status === "pending"
          ? "Pending"
          : invitation.status === "accepted"
            ? "Accepted"
            : "Declined"}
      </Badge>
      {isPending && (onResend || onRevoke) && (
        <div className="flex items-center gap-1">
          {onResend && (
            <Button
              variant="ghost"
              size="icon-sm"
              aria-label="Resend invitation"
              disabled={resendPending}
              onClick={() => onResend(invitation)}
            >
              <RotateCw className="size-4" />
            </Button>
          )}
          {onRevoke && (
            <Button
              variant="ghost"
              size="icon-sm"
              aria-label="Revoke invitation"
              onClick={() => onRevoke(invitation)}
            >
              <X className="size-4" />
            </Button>
          )}
        </div>
      )}
    </div>
  );
}
