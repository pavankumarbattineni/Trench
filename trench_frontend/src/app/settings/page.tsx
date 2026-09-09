"use client";

import { useRouter } from "next/navigation";
import { useEffect } from "react";

import { useAuth } from "@/components/auth-provider";
import { ChangePasswordForm } from "@/components/settings/change-password-form";
import { DangerZone } from "@/components/settings/danger-zone";
import { SettingsSection } from "@/components/settings/settings-section";
import { ThemeToggle } from "@/components/theme-toggle";
import { useFirebaseUser } from "@/hooks/use-firebase-user";

export default function SettingsPage() {
  const { user, loading } = useAuth();
  const { hasPassword } = useFirebaseUser();
  const router = useRouter();

  useEffect(() => {
    if (!loading && !user) {
      router.replace("/signin");
    }
  }, [loading, user, router]);

  if (loading || !user) {
    return (
      <main className="flex min-h-screen items-center justify-center bg-background text-foreground">
        <p className="text-sm text-muted-foreground">Loading…</p>
      </main>
    );
  }

  return (
    <main className="mx-auto flex min-h-screen max-w-2xl flex-col gap-8 bg-background px-4 py-12 text-foreground">
      <div>
        <h1 className="text-2xl font-bold tracking-tight">Settings</h1>
        <p className="text-sm text-muted-foreground">
          Signed in as <span className="font-medium text-foreground">{user.email}</span>
        </p>
      </div>

      <div className="divide-y divide-border rounded-2xl border border-border bg-card">
        <SettingsSection
          title="Appearance"
          description="Choose how Trench looks on this device."
        >
          <ThemeToggle />
        </SettingsSection>

        {hasPassword && (
          <SettingsSection
            title="Password"
            description={`Change the password for ${user.email}.`}
          >
            <ChangePasswordForm />
          </SettingsSection>
        )}

        <DangerZone />
      </div>
    </main>
  );
}
