"use client";

import { Crown, MoreHorizontal, ShieldCheck, UserMinus } from "lucide-react";
import { toast } from "sonner";

import { Avatar, AvatarFallback } from "@/components/ui/avatar";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { Switch } from "@/components/ui/switch";
import {
  Tooltip,
  TooltipContent,
  TooltipTrigger,
} from "@/components/ui/tooltip";
import { useToggleKnowledgeAccess, useUpdateMemberRole } from "@/hooks/use-organization";
import { getErrorMessage } from "@/lib/errors";
import type { OrganizationMember } from "@/lib/organizations";

interface MemberRowProps {
  member: OrganizationMember;
  organizationId: string;
  isCurrentUser: boolean;
  isAdmin: boolean;
  /** Only the Owner may promote a Member to Admin, demote an Admin back
   * to Member, or remove an Admin -- an Admin viewer can only remove
   * Members (see the permission matrix in the design spec). */
  isOwner: boolean;
  onRequestRemove: (member: OrganizationMember) => void;
}

export function MemberRow({
  member,
  organizationId,
  isCurrentUser,
  isAdmin,
  isOwner,
  onRequestRemove,
}: MemberRowProps) {
  const updateRole = useUpdateMemberRole(organizationId);
  const toggleAccess = useToggleKnowledgeAccess(organizationId);

  const handleToggleRole = () => {
    updateRole.mutate(
      { userId: member.user_id, role: member.role === "admin" ? "member" : "admin" },
      { onError: (err) => toast.error(getErrorMessage(err)) }
    );
  };

  const handleToggleAccess = (grant: boolean) => {
    toggleAccess.mutate(
      { userId: member.user_id, grant },
      { onError: (err) => toast.error(getErrorMessage(err)) }
    );
  };

  return (
    <div className="flex items-center gap-3 rounded-xl border border-border bg-card px-4 py-3">
      <Avatar size="sm">
        <AvatarFallback>{member.username[0]?.toUpperCase()}</AvatarFallback>
      </Avatar>
      <div className="min-w-0 flex-1">
        <p className="truncate text-sm font-medium">
          {member.username}
          {isCurrentUser && <span className="text-muted-foreground"> (you)</span>}
        </p>
        <p className="truncate text-xs text-muted-foreground">{member.email}</p>
      </div>

      {member.is_owner ? (
        <Badge variant="default" data-icon="inline-start">
          <Crown />
          Owner
        </Badge>
      ) : (
        <Badge variant={member.role === "admin" ? "default" : "outline"}>
          {member.role === "admin" && <ShieldCheck />}
          {member.role === "admin" ? "Admin" : "Member"}
        </Badge>
      )}

      {member.is_owner ? (
        // The owner always has full access -- showing a permanently-on,
        // permanently-disabled switch for them added nothing besides
        // visual noise, so this slot is just skipped for that row.
        <div className="w-8" />
      ) : (
        <Tooltip>
          <TooltipTrigger
            render={
              <div className="flex items-center gap-2">
                <Switch
                  checked={member.has_company_access}
                  disabled={
                    member.role === "admin" || !isAdmin || toggleAccess.isPending
                  }
                  onCheckedChange={handleToggleAccess}
                  aria-label="Company knowledge access"
                />
              </div>
            }
          />
          <TooltipContent>
            {member.role === "admin"
              ? "Admins always have company knowledge access"
              : "Company knowledge access -- lets this member select \"Company\" in chat and view organization documents"}
          </TooltipContent>
        </Tooltip>
      )}

      {(() => {
        // Permission matrix: only the Owner may promote/demote anyone, or
        // remove an Admin. A plain Admin may only remove a Member -- they
        // have no action at all on another Admin's row, and no role
        // control on anyone's row.
        const canRemove = isOwner || (isAdmin && member.role === "member");
        const hasAnyAction = !member.is_owner && canRemove;

        if (!hasAnyAction) {
          // Reserve the options button's width even with no menu, so
          // columns stay aligned across every row (owner row, and an
          // Admin viewer looking at another Admin's row).
          return <div className="size-7" />;
        }

        return (
          <DropdownMenu>
            <DropdownMenuTrigger
              render={
                <Button variant="ghost" size="icon-sm" aria-label="Member options">
                  <MoreHorizontal className="size-4" />
                </Button>
              }
            />
            <DropdownMenuContent align="end">
              {isOwner && (
                <DropdownMenuItem onClick={handleToggleRole} disabled={updateRole.isPending}>
                  <ShieldCheck className="size-4" />
                  {member.role === "admin" ? "Make member" : "Make admin"}
                </DropdownMenuItem>
              )}
              {canRemove && (
                <DropdownMenuItem
                  variant="destructive"
                  disabled={isCurrentUser}
                  onClick={() => onRequestRemove(member)}
                >
                  <UserMinus className="size-4" />
                  Remove
                </DropdownMenuItem>
              )}
            </DropdownMenuContent>
          </DropdownMenu>
        );
      })()}
    </div>
  );
}
