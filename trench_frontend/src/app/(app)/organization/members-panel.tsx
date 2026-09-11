"use client";

import { useState } from "react";
import {
  ChevronLeft,
  ChevronRight,
  MoreHorizontal,
  Plus,
  Search,
  ShieldCheck,
  ShieldOff,
  UserX,
  Users,
} from "lucide-react";
import { toast } from "sonner";

import { ConfirmDialog, useConfirmTarget } from "@/components/confirm-dialog";
import { Button } from "@/components/ui/button";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { Input } from "@/components/ui/input";
import { Skeleton } from "@/components/ui/skeleton";
import {
  useGrantAllKnowledgeAccess,
  useOrganizationMembers,
  useRemoveAllKnowledgeAccess,
  useRemoveAllMembers,
  useRemoveMember,
} from "@/hooks/use-organization";
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

type BulkAction = "allow-access" | "remove-access" | "remove-members";

const BULK_ACTION_COPY: Record<
  BulkAction,
  { title: string; description: string; confirmLabel: string }
> = {
  "allow-access": {
    title: "Grant company-knowledge access to everyone?",
    description:
      "Every current member will be granted company-knowledge access at once. You can still revoke access for individual members afterward.",
    confirmLabel: "Allow access",
  },
  "remove-access": {
    title: "Remove all company-knowledge access?",
    description:
      "Every member's company-knowledge access grant will be revoked at once. Org admins are unaffected -- they always have access. This can't be undone in bulk; you'd need to re-grant members individually.",
    confirmLabel: "Remove all access",
  },
  "remove-members": {
    title: "Remove all members?",
    description:
      "Every member will be removed from the organization at once, except the owner and yourself. This can't be undone -- removed members would need to be added back individually.",
    confirmLabel: "Remove all members",
  },
};

export function MembersPanel({
  organizationId,
  domain,
  isAdmin,
  currentUserId,
}: MembersPanelProps) {
  const [page, setPage] = useState(1);
  const [searchInput, setSearchInput] = useState("");
  const [search, setSearch] = useState("");
  const membersQuery = useOrganizationMembers(organizationId, { page, search });
  const removeMember = useRemoveMember(organizationId);
  const grantAllAccess = useGrantAllKnowledgeAccess(organizationId);
  const removeAllAccess = useRemoveAllKnowledgeAccess(organizationId);
  const removeAllMembers = useRemoveAllMembers(organizationId);
  const [addMemberOpen, setAddMemberOpen] = useState(false);
  const removeTarget = useConfirmTarget<OrganizationMember>();
  const [bulkAction, setBulkAction] = useState<BulkAction | null>(null);

  const handleSearchSubmit = (e: React.FormEvent) => {
    e.preventDefault();
    setPage(1);
    setSearch(searchInput.trim());
  };

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

  const bulkActionPending =
    grantAllAccess.isPending || removeAllAccess.isPending || removeAllMembers.isPending;

  const handleConfirmBulkAction = () => {
    if (bulkAction === "allow-access") {
      grantAllAccess.mutate(undefined, {
        onSuccess: (result) => {
          toast.success(
            `Company-knowledge access granted to ${result.granted_count} member${result.granted_count === 1 ? "" : "s"}.`
          );
          setBulkAction(null);
        },
        onError: (err) => {
          toast.error(getErrorMessage(err));
          setBulkAction(null);
        },
      });
    } else if (bulkAction === "remove-access") {
      removeAllAccess.mutate(undefined, {
        onSuccess: (result) => {
          toast.success(
            `Company-knowledge access removed for ${result.revoked_count} member${result.revoked_count === 1 ? "" : "s"}.`
          );
          setBulkAction(null);
        },
        onError: (err) => {
          toast.error(getErrorMessage(err));
          setBulkAction(null);
        },
      });
    } else if (bulkAction === "remove-members") {
      removeAllMembers.mutate(undefined, {
        onSuccess: (result) => {
          toast.success(
            `${result.removed_count} member${result.removed_count === 1 ? "" : "s"} removed from the organization.`
          );
          setBulkAction(null);
        },
        onError: (err) => {
          toast.error(getErrorMessage(err));
          setBulkAction(null);
        },
      });
    }
  };

  const total = membersQuery.data?.total ?? 0;
  const totalPages = membersQuery.data?.total_pages ?? 1;

  return (
    <div className="flex flex-col gap-4">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <div className="flex items-center gap-2">
          <Users className="size-4 text-muted-foreground" />
          <h2 className="text-sm font-medium">
            {total} member{total === 1 ? "" : "s"}
          </h2>
        </div>
        {isAdmin && (
          <div className="flex items-center gap-2">
            <DropdownMenu>
              <DropdownMenuTrigger
                render={
                  <Button size="sm" variant="outline">
                    <MoreHorizontal className="size-4" />
                    Bulk actions
                  </Button>
                }
              />
              <DropdownMenuContent align="end">
                <DropdownMenuItem onClick={() => setBulkAction("allow-access")}>
                  <ShieldCheck className="size-4" />
                  Allow access
                </DropdownMenuItem>
                <DropdownMenuItem onClick={() => setBulkAction("remove-access")}>
                  <ShieldOff className="size-4" />
                  Remove all access
                </DropdownMenuItem>
                <DropdownMenuItem
                  variant="destructive"
                  onClick={() => setBulkAction("remove-members")}
                >
                  <UserX className="size-4" />
                  Remove all members
                </DropdownMenuItem>
              </DropdownMenuContent>
            </DropdownMenu>
            <Button size="sm" onClick={() => setAddMemberOpen(true)}>
              <Plus className="size-4" />
              Add member
            </Button>
          </div>
        )}
      </div>

      <form onSubmit={handleSearchSubmit} className="relative">
        <Search className="pointer-events-none absolute left-2.5 top-1/2 size-4 -translate-y-1/2 text-muted-foreground" />
        <Input
          value={searchInput}
          onChange={(e) => setSearchInput(e.target.value)}
          placeholder="Search by username or email…"
          className="pl-8"
        />
      </form>

      <div className="flex flex-col gap-2">
        {membersQuery.isLoading &&
          Array.from({ length: 3 }).map((_, i) => (
            <Skeleton key={i} className="h-16 w-full rounded-xl" />
          ))}

        {membersQuery.isError && (
          <p className="text-sm text-destructive">{getErrorMessage(membersQuery.error)}</p>
        )}

        {!membersQuery.isLoading && membersQuery.data?.items.length === 0 && (
          <p className="px-1 py-6 text-center text-sm text-muted-foreground">
            No members match &quot;{search}&quot;.
          </p>
        )}

        {membersQuery.data?.items.map((member) => (
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

      {totalPages > 1 && (
        <div className="flex items-center justify-between text-sm text-muted-foreground">
          <span>
            Page {page} of {totalPages}
          </span>
          <div className="flex gap-1">
            <Button
              size="icon-sm"
              variant="outline"
              disabled={page <= 1}
              onClick={() => setPage((p) => Math.max(1, p - 1))}
              aria-label="Previous page"
            >
              <ChevronLeft className="size-4" />
            </Button>
            <Button
              size="icon-sm"
              variant="outline"
              disabled={page >= totalPages}
              onClick={() => setPage((p) => Math.min(totalPages, p + 1))}
              aria-label="Next page"
            >
              <ChevronRight className="size-4" />
            </Button>
          </div>
        </div>
      )}

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

      <ConfirmDialog
        open={bulkAction !== null}
        onOpenChange={(open) => !open && setBulkAction(null)}
        title={bulkAction ? BULK_ACTION_COPY[bulkAction].title : ""}
        description={bulkAction ? BULK_ACTION_COPY[bulkAction].description : ""}
        confirmLabel={bulkAction ? BULK_ACTION_COPY[bulkAction].confirmLabel : "Confirm"}
        isPending={bulkActionPending}
        onConfirm={handleConfirmBulkAction}
      />
    </div>
  );
}
