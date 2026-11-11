"use client";

import { useAuth } from "@/components/auth-provider";
import { TruncatedEmail } from "@/components/truncated-email";
import { SettingsNav } from "./settings-nav";

export default function SettingsLayout({ children }: { children: React.ReactNode }) {
  const { user } = useAuth();
  if (!user) return null;

  return (
    <main className="mx-auto flex h-full max-w-4xl flex-col gap-6 overflow-y-auto px-4 py-10 sm:gap-8">
      <div className="min-w-0">
        <h1 className="text-2xl font-bold tracking-tight">Settings</h1>
        <p className="flex min-w-0 gap-1 text-sm text-muted-foreground">
          <span className="shrink-0">Signed in as</span>
          <TruncatedEmail email={user.email} className="font-medium text-foreground" />
        </p>
      </div>

      <div className="flex flex-col gap-6 sm:flex-row sm:gap-8">
        <SettingsNav />
        <div className="min-w-0 flex-1">{children}</div>
      </div>
    </main>
  );
}
