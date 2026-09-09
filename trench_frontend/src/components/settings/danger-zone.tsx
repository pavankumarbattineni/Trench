"use client";

import { useRouter } from "next/navigation";
import { useState } from "react";

import { useAuth } from "@/components/auth-provider";
import { SettingsSection } from "@/components/settings/settings-section";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { useFirebaseUser } from "@/hooks/use-firebase-user";
import {
  deleteAccountWithGoogle,
  deleteAccountWithPassword,
  signOutEverywhere,
} from "@/lib/auth-service";
import { getErrorMessage } from "@/lib/errors";

const DELETE_CONFIRMATION_TEXT = "DELETE";

export function DangerZone() {
  const router = useRouter();
  const { setUser } = useAuth();
  const { ready, hasPassword } = useFirebaseUser();

  const [loggingOut, setLoggingOut] = useState(false);
  const [showDeleteForm, setShowDeleteForm] = useState(false);
  const [password, setPassword] = useState("");
  const [confirmText, setConfirmText] = useState("");
  const [deleting, setDeleting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const handleLogout = async () => {
    setLoggingOut(true);
    try {
      await signOutEverywhere();
    } finally {
      setUser(null);
      router.replace("/signin");
    }
  };

  const finishDeletion = () => {
    setUser(null);
    router.replace("/signin");
  };

  const handleDeleteWithPassword = async () => {
    setError(null);
    setDeleting(true);
    try {
      await deleteAccountWithPassword(password);
      finishDeletion();
    } catch (err) {
      setError(getErrorMessage(err));
      setDeleting(false);
    }
  };

  const handleDeleteWithGoogle = async () => {
    setError(null);
    setDeleting(true);
    try {
      await deleteAccountWithGoogle();
      finishDeletion();
    } catch (err) {
      setError(getErrorMessage(err));
      setDeleting(false);
    }
  };

  return (
    <SettingsSection title="Danger zone" description="Logging out and account deletion.">
      <div className="space-y-6">
        <div className="flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
          <p className="text-sm text-muted-foreground">Sign out of Trench on this device.</p>
          <Button
            variant="outline"
            className="self-start sm:self-auto"
            onClick={handleLogout}
            disabled={loggingOut}
          >
            {loggingOut ? "Logging out…" : "Log out"}
          </Button>
        </div>

        <div className="border-t border-border pt-6">
          {!showDeleteForm ? (
            <div className="flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
              <p className="text-sm text-muted-foreground">
                Permanently delete your account and all associated data.
              </p>
              <Button
                variant="destructive"
                className="self-start sm:self-auto"
                onClick={() => setShowDeleteForm(true)}
              >
                Delete account
              </Button>
            </div>
          ) : !ready ? (
            <p className="text-sm text-muted-foreground">Loading account info…</p>
          ) : (
            <div className="space-y-4">
              <p className="text-sm text-destructive">
                This cannot be undone. Type <strong>{DELETE_CONFIRMATION_TEXT}</strong> to
                confirm{hasPassword ? " and enter your password" : ""}.
              </p>
              <div className="space-y-2">
                <Label htmlFor="delete-confirm">
                  Type {DELETE_CONFIRMATION_TEXT} to confirm
                </Label>
                <Input
                  id="delete-confirm"
                  value={confirmText}
                  onChange={(event) => setConfirmText(event.target.value)}
                />
              </div>
              {hasPassword && (
                <div className="space-y-2">
                  <Label htmlFor="delete-password">Current password</Label>
                  <Input
                    id="delete-password"
                    type="password"
                    value={password}
                    onChange={(event) => setPassword(event.target.value)}
                    autoComplete="current-password"
                  />
                </div>
              )}
              {error && <p className="text-sm text-destructive">{error}</p>}
              <div className="flex gap-2">
                {hasPassword ? (
                  <Button
                    variant="destructive"
                    disabled={
                      deleting || !password || confirmText !== DELETE_CONFIRMATION_TEXT
                    }
                    onClick={handleDeleteWithPassword}
                  >
                    {deleting ? "Deleting…" : "Permanently delete account"}
                  </Button>
                ) : (
                  <Button
                    variant="destructive"
                    disabled={deleting || confirmText !== DELETE_CONFIRMATION_TEXT}
                    onClick={handleDeleteWithGoogle}
                  >
                    {deleting ? "Deleting…" : "Confirm with Google & delete"}
                  </Button>
                )}
                <Button
                  variant="outline"
                  onClick={() => {
                    setShowDeleteForm(false);
                    setPassword("");
                    setConfirmText("");
                    setError(null);
                  }}
                >
                  Cancel
                </Button>
              </div>
            </div>
          )}
        </div>
      </div>
    </SettingsSection>
  );
}
