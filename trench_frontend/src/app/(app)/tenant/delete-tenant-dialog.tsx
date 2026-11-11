"use client";

import { useState } from "react";
import { useRouter } from "next/navigation";
import { AlertTriangle } from "lucide-react";
import { toast } from "sonner";

import { useAuth } from "@/components/auth-provider";
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
import { useDeleteTenant } from "@/hooks/use-tenant";
import { getErrorMessage } from "@/lib/errors";

interface DeleteTenantDialogProps {
  tenantId: string;
  tenantName: string;
  open: boolean;
  onOpenChange: (open: boolean) => void;
}

/** Owner-only, irreversible: deletes the tenant after the Owner types its
 * exact name. Members (the Owner included) keep their accounts. */
export function DeleteTenantDialog({
  tenantId,
  tenantName,
  open,
  onOpenChange,
}: DeleteTenantDialogProps) {
  const router = useRouter();
  const { refreshUser } = useAuth();
  const deleteTenant = useDeleteTenant(tenantId);
  const [confirmName, setConfirmName] = useState("");
  const [error, setError] = useState<string | null>(null);
  const matches = confirmName.trim() === tenantName;

  const handleOpenChange = (next: boolean) => {
    if (deleteTenant.isPending) return;
    if (!next) {
      setConfirmName("");
      setError(null);
    }
    onOpenChange(next);
  };

  const handleDelete = () => {
    if (!matches) return;
    setError(null);
    deleteTenant.mutate(confirmName, {
      onSuccess: (result) => {
        toast.success(
          `"${tenantName}" was deleted. ${result.members_removed} member${
            result.members_removed === 1 ? "" : "s"
          } removed, ${result.documents_deleted} document${
            result.documents_deleted === 1 ? "" : "s"
          } deleted.`
        );
        // The caller's role and company access changed server-side.
        void refreshUser();
        onOpenChange(false);
        router.replace("/chat");
      },
      onError: (err) => setError(getErrorMessage(err)),
    });
  };

  return (
    <Dialog open={open} onOpenChange={handleOpenChange}>
      <DialogContent>
        <DialogHeader>
          <DialogTitle className="flex items-center gap-2 text-destructive">
            <AlertTriangle className="size-5" />
            Delete tenant
          </DialogTitle>
          <DialogDescription>
            This permanently deletes <strong className="text-foreground">{tenantName}</strong>{" "}
            and can&apos;t be undone.
          </DialogDescription>
        </DialogHeader>

        <div className="flex flex-col gap-3 rounded-lg border border-destructive/30 bg-destructive/5 p-3 text-sm">
          <p className="font-medium text-destructive">What will be deleted</p>
          <ul className="list-disc space-y-1 pl-5 text-muted-foreground">
            <li>
              <span className="text-foreground">All users</span>, including you, are removed from
              this tenant. Their accounts, personal documents and chats are kept, but they will no
              longer be part of any tenant.
            </li>
            <li>
              <span className="text-foreground">All company documents</span> and their search
              data.
            </li>
            <li>Pending invitations and the tenant&apos;s shared API key.</li>
          </ul>
        </div>

        <div className="flex flex-col gap-2">
          <Label htmlFor="delete-tenant-confirm" className="font-normal">
            Type <strong className="break-all">{tenantName}</strong> to confirm
          </Label>
          <Input
            id="delete-tenant-confirm"
            value={confirmName}
            onChange={(e) => setConfirmName(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Enter") handleDelete();
            }}
            placeholder={tenantName}
            autoComplete="off"
            disabled={deleteTenant.isPending}
          />
          {error && <p className="text-sm text-destructive">{error}</p>}
        </div>

        <DialogFooter>
          <Button
            variant="outline"
            onClick={() => handleOpenChange(false)}
            disabled={deleteTenant.isPending}
          >
            Cancel
          </Button>
          <Button
            variant="destructive"
            onClick={handleDelete}
            disabled={!matches || deleteTenant.isPending}
          >
            {deleteTenant.isPending ? "Deleting…" : "Delete tenant"}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
