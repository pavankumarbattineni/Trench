"use client";

import { useState } from "react";
import { CheckCircle2, Mail, Plus, Upload, XCircle } from "lucide-react";
import { toast } from "sonner";

import { ConfirmDialog, useConfirmTarget } from "@/components/confirm-dialog";
import {
  Accordion,
  AccordionItem,
  AccordionPanel,
  AccordionTrigger,
} from "@/components/ui/accordion";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/skeleton";
import {
  useInvitations,
  useResendInvitation,
  useRevokeInvitation,
} from "@/hooks/use-tenant";
import { getErrorMessage } from "@/lib/errors";
import type { Invitation } from "@/lib/tenants";
import { BulkInviteDialog } from "./bulk-invite-dialog";
import { InvitationRow } from "./invitation-row";
import { InviteMemberDialog } from "./invite-member-dialog";

interface InvitationsTabProps {
  tenantId: string;
  isAdmin: boolean;
  isOwner: boolean;
}

type SectionKey = "accepted" | "pending" | "declined";

const SECTION_META: Record<
  SectionKey,
  { label: string; icon: typeof Mail; emptyLabel: string }
> = {
  accepted: {
    label: "Accepted",
    icon: CheckCircle2,
    emptyLabel: "No accepted invitations yet.",
  },
  pending: {
    label: "Pending",
    icon: Mail,
    emptyLabel: "No pending invitations.",
  },
  declined: {
    label: "Declined",
    icon: XCircle,
    emptyLabel: "No declined invitations.",
  },
};

function sectionFor(invitation: Invitation): SectionKey {
  if (invitation.status === "pending") return "pending";
  if (invitation.status === "accepted") return "accepted";
  // "revoked" and "expired" both mean "this invite will never become a
  // membership" -- grouped under one "Declined" section client-side,
  // since there's no distinct invitee-initiated decline action in the
  // backend yet (see invitation-row.tsx's note).
  return "declined";
}

export function InvitationsTab({ tenantId, isAdmin, isOwner }: InvitationsTabProps) {
  const invitationsQuery = useInvitations(tenantId);
  const resendInvitation = useResendInvitation(tenantId);
  const revokeInvitation = useRevokeInvitation(tenantId);
  const revokeTarget = useConfirmTarget<Invitation>();
  const [inviteOpen, setInviteOpen] = useState(false);
  const [bulkInviteOpen, setBulkInviteOpen] = useState(false);

  const grouped: Record<SectionKey, Invitation[]> = {
    accepted: [],
    pending: [],
    declined: [],
  };
  for (const invitation of invitationsQuery.data ?? []) {
    grouped[sectionFor(invitation)].push(invitation);
  }

  const handleResend = (invitation: Invitation) => {
    resendInvitation.mutate(invitation.id, {
      onSuccess: () => toast.success(`Invitation resent to ${invitation.email}.`),
      onError: (err) => toast.error(getErrorMessage(err)),
    });
  };

  const handleConfirmRevoke = () => {
    if (!revokeTarget.target) return;
    revokeInvitation.mutate(revokeTarget.target.id, {
      onSuccess: () => {
        toast.success(`Invitation to ${revokeTarget.target?.email} revoked.`);
        revokeTarget.clear();
      },
      onError: (err) => {
        toast.error(getErrorMessage(err));
        revokeTarget.clear();
      },
    });
  };

  if (!isAdmin) {
    return (
      <p className="px-1 py-6 text-center text-sm text-muted-foreground">
        Only an Owner or Admin can view invitations.
      </p>
    );
  }

  return (
    <div className="flex flex-col gap-4">
      <div className="flex items-center justify-between gap-2">
        <h2 className="text-sm font-medium">Invitations</h2>
        <div className="flex items-center gap-2">
          <Button size="sm" variant="outline" onClick={() => setBulkInviteOpen(true)}>
            <Upload className="size-4" />
            Bulk upload
          </Button>
          <Button size="sm" onClick={() => setInviteOpen(true)}>
            <Plus className="size-4" />
            Invite
          </Button>
        </div>
      </div>

      {invitationsQuery.isLoading && (
        <div className="flex flex-col gap-3">
          <Skeleton className="h-11 w-full rounded-xl" />
          <Skeleton className="h-11 w-full rounded-xl" />
          <Skeleton className="h-11 w-full rounded-xl" />
        </div>
      )}

      {invitationsQuery.isError && (
        <p className="text-sm text-destructive">
          {getErrorMessage(invitationsQuery.error)}
        </p>
      )}

      {!invitationsQuery.isLoading && !invitationsQuery.isError && (
        <Accordion defaultValue={[]} multiple>
          {(["accepted", "pending", "declined"] as const).map((key) => {
            const meta = SECTION_META[key];
            const Icon = meta.icon;
            const items = grouped[key];
            return (
              <AccordionItem key={key} value={key}>
                <AccordionTrigger>
                  <span className="flex items-center gap-2">
                    <Icon className="size-4 text-muted-foreground" />
                    {meta.label}
                    <Badge variant="outline">{items.length}</Badge>
                  </span>
                </AccordionTrigger>
                <AccordionPanel>
                  {items.length === 0 ? (
                    <p className="py-2 text-sm text-muted-foreground">
                      {meta.emptyLabel}
                    </p>
                  ) : (
                    items.map((invitation) => (
                      <InvitationRow
                        key={invitation.id}
                        invitation={invitation}
                        onResend={
                          invitation.status === "pending" ? handleResend : undefined
                        }
                        onRevoke={
                          invitation.status === "pending"
                            ? revokeTarget.request
                            : undefined
                        }
                        resendPending={resendInvitation.isPending}
                      />
                    ))
                  )}
                </AccordionPanel>
              </AccordionItem>
            );
          })}
        </Accordion>
      )}

      <InviteMemberDialog
        tenantId={tenantId}
        callerRole={isOwner ? "owner" : "admin"}
        open={inviteOpen}
        onOpenChange={setInviteOpen}
      />
      <BulkInviteDialog
        tenantId={tenantId}
        open={bulkInviteOpen}
        onOpenChange={setBulkInviteOpen}
      />

      <ConfirmDialog
        open={revokeTarget.open}
        onOpenChange={(open) => !open && revokeTarget.clear()}
        title="Revoke invitation?"
        description={`${revokeTarget.target?.email} will no longer be able to use this invitation link to join.`}
        confirmLabel="Revoke"
        isPending={revokeInvitation.isPending}
        onConfirm={handleConfirmRevoke}
      />
    </div>
  );
}
