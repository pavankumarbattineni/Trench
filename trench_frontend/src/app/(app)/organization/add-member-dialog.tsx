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
import { useAddMember } from "@/hooks/use-organization";
import { getErrorMessage } from "@/lib/errors";
import type { OrganizationRole } from "@/lib/organizations";

const ROLE_ITEMS: Record<OrganizationRole, string> = {
  member: "Member",
  admin: "Admin",
};

interface AddMemberDialogProps {
  organizationId: string;
  domain: string;
  open: boolean;
  onOpenChange: (open: boolean) => void;
}

export function AddMemberDialog({
  organizationId,
  domain,
  open,
  onOpenChange,
}: AddMemberDialogProps) {
  const [username, setUsername] = useState("");
  const [role, setRole] = useState<OrganizationRole>("member");
  const [error, setError] = useState<string | null>(null);
  const addMember = useAddMember(organizationId);

  const handleSubmit = () => {
    const trimmed = username.trim();
    if (!trimmed) return;
    setError(null);
    addMember.mutate(
      { username: trimmed, role },
      {
        onSuccess: () => {
          setUsername("");
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
          <DialogTitle>Add a member</DialogTitle>
          <DialogDescription>
            They must already have a Trench account with a @{domain} email address.
          </DialogDescription>
        </DialogHeader>
        <div className="space-y-4">
          <div className="space-y-2">
            <Label htmlFor="member-username">Username</Label>
            <Input
              id="member-username"
              value={username}
              onChange={(e) => setUsername(e.target.value)}
              placeholder="jane_doe"
              autoFocus
              onKeyDown={(e) => e.key === "Enter" && handleSubmit()}
            />
          </div>
          <div className="space-y-2">
            <Label>Role</Label>
            <Select
              items={ROLE_ITEMS}
              value={role}
              onValueChange={(value) => value && setRole(value as OrganizationRole)}
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
          {error && <p className="text-sm text-destructive">{error}</p>}
        </div>
        <DialogFooter>
          <Button variant="outline" onClick={() => onOpenChange(false)}>
            Cancel
          </Button>
          <Button onClick={handleSubmit} disabled={!username.trim() || addMember.isPending}>
            {addMember.isPending ? "Adding…" : "Add member"}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
