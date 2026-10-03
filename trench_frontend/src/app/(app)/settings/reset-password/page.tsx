"use client";

import { ShieldCheck } from "lucide-react";

import { useAuth } from "@/components/auth-provider";
import { ChangePasswordForm } from "@/components/settings/change-password-form";
import { SettingsSection } from "@/components/settings/settings-section";
import { useFirebaseUser } from "@/hooks/use-firebase-user";

export default function ResetPasswordPage() {
  const { user } = useAuth();
  const { ready, hasPassword } = useFirebaseUser();

  return (
    <div className="rounded-2xl border border-border bg-card">
      <SettingsSection
        title="Reset Password"
        description={
          hasPassword
            ? `Change the password for ${user?.email}.`
            : "Manage your account's password."
        }
      >
        {!ready ? (
          <div className="h-40 animate-pulse rounded-xl bg-muted" />
        ) : hasPassword ? (
          <ChangePasswordForm />
        ) : (
          <div className="flex items-start gap-3 rounded-xl border border-border bg-muted/40 p-4">
            <ShieldCheck className="mt-0.5 size-5 shrink-0 text-muted-foreground" />
            <p className="text-sm text-muted-foreground">
              Your account signs in with Google and has no password to reset. If
              you&apos;d like to add one, contact support -- for now, continue signing
              in with Google.
            </p>
          </div>
        )}
      </SettingsSection>
    </div>
  );
}
