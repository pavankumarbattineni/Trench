"use client";

import { useAuth } from "@/components/auth-provider";
import { SettingsNav } from "./settings-nav";

export default function SettingsLayout({ children }: { children: React.ReactNode }) {
  const { user } = useAuth();
  if (!user) return null;

  return (
    <main className="mx-auto flex h-full max-w-4xl flex-col gap-6 overflow-y-auto px-4 py-10 sm:gap-8">
      <div>
        <h1 className="text-2xl font-bold tracking-tight">Settings</h1>
        <p className="text-sm text-muted-foreground">
          Signed in as <span className="font-medium text-foreground">{user.email}</span>
        </p>
      </div>

      <div className="flex flex-col gap-6 sm:flex-row sm:gap-8">
        <SettingsNav />
        <div className="min-w-0 flex-1">{children}</div>
      </div>
    </main>
  );
}
