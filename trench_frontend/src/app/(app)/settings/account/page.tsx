"use client";

import { useAuth } from "@/components/auth-provider";
import { DeleteAccount } from "@/components/settings/delete-account";
import { SettingsSection } from "@/components/settings/settings-section";

export default function AccountPage() {
  const { user } = useAuth();
  if (!user) return null;

  return (
    <div className="flex flex-col gap-6">
      <div className="rounded-2xl border border-border bg-card">
        <SettingsSection title="Account" description="Your account details.">
          <dl className="grid grid-cols-[max-content_1fr] gap-x-4 gap-y-2 text-sm">
            <dt className="text-muted-foreground">Username</dt>
            <dd className="font-medium">{user.username}</dd>
            <dt className="text-muted-foreground">Email</dt>
            <dd className="font-medium">{user.email}</dd>
            <dt className="text-muted-foreground">Member since</dt>
            <dd className="font-medium">
              {new Date(user.created_at).toLocaleDateString()}
            </dd>
            {user.organization && (
              <>
                <dt className="text-muted-foreground">Organization</dt>
                <dd className="font-medium">
                  {user.organization.name} ({user.organization.role})
                </dd>
              </>
            )}
          </dl>
        </SettingsSection>
      </div>

      <div className="rounded-2xl border border-destructive/30 bg-card">
        <SettingsSection
          title="Danger zone"
          description="Irreversible account actions."
        >
          <DeleteAccount />
        </SettingsSection>
      </div>
    </div>
  );
}
