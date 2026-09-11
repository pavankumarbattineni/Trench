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
  onRequestRemove: (member: OrganizationMember) => void;
}

export function MemberRow({
  member,
  organizationId,
  isCurrentUser,
  isAdmin,
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

      {isAdmin && !member.is_owner && (
        <DropdownMenu>
          <DropdownMenuTrigger
            render={
              <Button variant="ghost" size="icon-sm" aria-label="Member options">
                <MoreHorizontal className="size-4" />
              </Button>
            }
          />
          <DropdownMenuContent align="end">
            <DropdownMenuItem onClick={handleToggleRole} disabled={updateRole.isPending}>
              <ShieldCheck className="size-4" />
              {member.role === "admin" ? "Make member" : "Make admin"}
            </DropdownMenuItem>
            <DropdownMenuItem
              variant="destructive"
              disabled={isCurrentUser}
              onClick={() => onRequestRemove(member)}
            >
              <UserMinus className="size-4" />
              Remove
            </DropdownMenuItem>
          </DropdownMenuContent>
        </DropdownMenu>
      )}
      {/* Reserve the options button's width for the owner row too, so
          columns stay aligned across all rows. */}
      {isAdmin && member.is_owner && <div className="size-7" />}
    </div>
  );
}
