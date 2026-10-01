"use client";

import { useState } from "react";

import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { useCreateInvitation } from "@/hooks/use-organization";
import { getErrorMessage } from "@/lib/errors";
import type { InvitationRole } from "@/lib/organizations";

const ROLE_ITEMS: Record<InvitationRole, string> = {
  member: "Member",
  admin: "Admin",
};

interface InviteMemberDialogProps {
  organizationId: string;
  /** The inviter's own role -- Admins may only invite Members; only the
   * Owner may invite as Admin. The backend enforces this too, but the
   * dialog shouldn't offer a choice the caller can't actually make. */
  callerRole: "owner" | "admin";
  open: boolean;
  onOpenChange: (open: boolean) => void;
}

export function InviteMemberDialog({
  organizationId,
  callerRole,
  open,
  onOpenChange,
}: InviteMemberDialogProps) {
  const [email, setEmail] = useState("");
  const [role, setRole] = useState<InvitationRole>("member");
  const [error, setError] = useState<string | null>(null);
  const createInvitation = useCreateInvitation(organizationId);
  const canInviteAdmins = callerRole === "owner";

  const handleSubmit = () => {
    const trimmed = email.trim();
    if (!trimmed) return;
    setError(null);
    createInvitation.mutate(
      { email: trimmed, role },
      {
        onSuccess: () => {
          setEmail("");
          setRole("member");
          onOpenChange(false);
        },
        onError: (err) => setError(getErrorMessage(err)),
      }
    );
  };

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>Invite an employee</DialogTitle>
          <DialogDescription>
            They&apos;ll receive an email invitation with a link to join the
            organization.
          </DialogDescription>
        </DialogHeader>
        <div className="space-y-4">
          <div className="space-y-2">
            <Label htmlFor="invite-email">Email</Label>
            <Input
              id="invite-email"
              type="email"
              value={email}
              onChange={(e) => setEmail(e.target.value)}
              placeholder="jane@yourcompany.com"
              autoFocus
              onKeyDown={(e) => e.key === "Enter" && handleSubmit()}
            />
          </div>
          {canInviteAdmins && (
            <div className="space-y-2">
              <Label>Role</Label>
              <Select
                items={ROLE_ITEMS}
                value={role}
                onValueChange={(value) => value && setRole(value as InvitationRole)}
              >
                <SelectTrigger className="w-full">
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  <SelectItem value="member">Member</SelectItem>
                  <SelectItem value="admin">Admin</SelectItem>
                </SelectContent>
              </Select>
            </div>
          )}
          {error && <p className="text-sm text-destructive">{error}</p>}
        </div>
        <DialogFooter>
          <Button variant="outline" onClick={() => onOpenChange(false)}>
            Cancel
          </Button>
          <Button
            onClick={handleSubmit}
            disabled={!email.trim() || createInvitation.isPending}
          >
            {createInvitation.isPending ? "Sending…" : "Send invitation"}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
