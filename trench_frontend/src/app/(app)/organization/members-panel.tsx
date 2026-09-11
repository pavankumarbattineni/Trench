"use client";

import { useState } from "react";
import { Plus, Users } from "lucide-react";
import { toast } from "sonner";

import { ConfirmDialog, useConfirmTarget } from "@/components/confirm-dialog";
import { Button } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/skeleton";
import { useOrganizationMembers, useRemoveMember } from "@/hooks/use-organization";
import { getErrorMessage } from "@/lib/errors";
import type { OrganizationMember } from "@/lib/organizations";
import { AddMemberDialog } from "./add-member-dialog";
import { MemberRow } from "./member-row";

interface MembersPanelProps {
  organizationId: string;
  domain: string;
  isAdmin: boolean;
  currentUserId: string | undefined;
}

export function MembersPanel({
  organizationId,
  domain,
  isAdmin,
  currentUserId,
}: MembersPanelProps) {
  const membersQuery = useOrganizationMembers(organizationId);
  const removeMember = useRemoveMember(organizationId);
  const [addMemberOpen, setAddMemberOpen] = useState(false);
  const removeTarget = useConfirmTarget<OrganizationMember>();

  const handleConfirmRemove = () => {
    if (!removeTarget.target) return;
    removeMember.mutate(removeTarget.target.user_id, {
      onSuccess: () => {
        toast.success(`${removeTarget.target?.username} removed from the organization.`);
        removeTarget.clear();
      },
      onError: (err) => {
        toast.error(getErrorMessage(err));
        removeTarget.clear();
      },
    });
  };

  return (
    <div className="flex flex-col gap-4">
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-2">
          <Users className="size-4 text-muted-foreground" />
          <h2 className="text-sm font-medium">
            {membersQuery.data?.length ?? 0} member
            {membersQuery.data?.length === 1 ? "" : "s"}
          </h2>
        </div>
        {isAdmin && (
          <Button size="sm" onClick={() => setAddMemberOpen(true)}>
            <Plus className="size-4" />
            Add member
          </Button>
        )}
      </div>

      <div className="flex flex-col gap-2">
        {membersQuery.isLoading &&
          Array.from({ length: 3 }).map((_, i) => (
            <Skeleton key={i} className="h-16 w-full rounded-xl" />
          ))}

        {membersQuery.isError && (
          <p className="text-sm text-destructive">{getErrorMessage(membersQuery.error)}</p>
        )}

        {membersQuery.data?.map((member) => (
          <MemberRow
            key={member.id}
            member={member}
            organizationId={organizationId}
            isCurrentUser={member.user_id === currentUserId}
            isAdmin={isAdmin}
            onRequestRemove={removeTarget.request}
          />
        ))}
      </div>

      {isAdmin && (
        <AddMemberDialog
          organizationId={organizationId}
          domain={domain}
          open={addMemberOpen}
          onOpenChange={setAddMemberOpen}
        />
      )}

      <ConfirmDialog
        open={removeTarget.open}
        onOpenChange={(open) => !open && removeTarget.clear()}
        title="Remove member?"
        description={`${removeTarget.target?.username} will lose access to this organization and its company knowledge base.`}
        confirmLabel="Remove"
        isPending={removeMember.isPending}
        onConfirm={handleConfirmRemove}
      />
    </div>
  );
}
