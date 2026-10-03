"use client";

import { useEffect, useState } from "react";
import { useTheme } from "next-themes";
import { Check, Moon, Sun } from "lucide-react";

import { SettingsSection } from "@/components/settings/settings-section";
import { cn } from "cn";

const THEMES = [
  { value: "light", label: "Light", icon: Sun },
  { value: "dark", label: "Dark", icon: Moon },
] as const;

export default function AppearancePage() {
  const { theme, setTheme } = useTheme();
  const [mounted, setMounted] = useState(false);

  useEffect(() => {
    // Client-only mount gate for hydration safety: the server can't know
    // the persisted theme, so this must run post-hydration, not during
    // render.
    // eslint-disable-next-line react-hooks/set-state-in-effect
    setMounted(true);
  }, []);

  return (
    <div className="rounded-2xl border border-border bg-card">
      <SettingsSection
        title="Appearance"
        description="Choose how Trench looks on this device. This is saved to your browser and applies everywhere you use Trench."
      >
        {!mounted ? (
          <div className="grid grid-cols-2 gap-3">
            {THEMES.map((t) => (
              <div key={t.value} className="h-20 animate-pulse rounded-xl bg-muted" />
            ))}
          </div>
        ) : (
          <div className="grid grid-cols-2 gap-3">
            {THEMES.map((t) => {
              const Icon = t.icon;
              const active = theme === t.value;
              return (
                <button
                  key={t.value}
                  type="button"
                  onClick={() => setTheme(t.value)}
                  className={cn(
                    "relative flex flex-col items-center gap-2 rounded-xl border p-4 transition-colors",
                    active
                      ? "border-primary bg-primary/5"
                      : "border-border hover:bg-muted"
                  )}
                >
                  {active && (
                    <span className="absolute top-2 right-2 flex size-5 items-center justify-center rounded-full bg-primary text-primary-foreground">
                      <Check className="size-3" />
                    </span>
                  )}
                  <Icon className="size-6" />
                  <span className="text-sm font-medium">{t.label}</span>
                </button>
              );
            })}
          </div>
        )}
      </SettingsSection>
    </div>
  );
}
