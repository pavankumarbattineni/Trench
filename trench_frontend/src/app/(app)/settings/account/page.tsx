"use client";

import { useAuth } from "@/components/auth-provider";
import { DeleteAccount } from "@/components/settings/delete-account";
import { SettingsSection } from "@/components/settings/settings-section";
import { TruncatedEmail } from "@/components/truncated-email";

export default function AccountPage() {
  const { user } = useAuth();
  if (!user) return null;

  return (
    <div className="flex flex-col gap-6">
      <div className="rounded-2xl border border-border bg-card">
        <SettingsSection title="Account" description="Your account details.">
          {/* minmax(0,1fr): lets the value column shrink below its longest
              word, so long values truncate instead of overflowing the card. */}
          <dl className="grid grid-cols-[max-content_minmax(0,1fr)] gap-x-4 gap-y-2 text-sm">
            <dt className="text-muted-foreground">Username</dt>
            <dd className="min-w-0 truncate font-medium" title={user.username}>
              {user.username}
            </dd>
            <dt className="text-muted-foreground">Email</dt>
            <dd className="min-w-0 font-medium">
              <TruncatedEmail email={user.email} />
            </dd>
            <dt className="text-muted-foreground">Member since</dt>
            <dd className="font-medium">
              {new Date(user.created_at).toLocaleDateString()}
            </dd>
            {user.tenant && (
              <>
                <dt className="text-muted-foreground">Tenant</dt>
                <dd
                  className="min-w-0 truncate font-medium"
                  title={`${user.tenant.name} (${user.tenant.role})`}
                >
                  {user.tenant.name} ({user.tenant.role})
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
