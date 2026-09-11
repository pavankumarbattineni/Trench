"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { KeyRound, Palette, ShieldCheck, UserCog } from "lucide-react";

import { Button } from "@/components/ui/button";
import { cn } from "cn";

const NAV_ITEMS = [
  { href: "/settings/appearance", label: "Appearance", icon: Palette },
  { href: "/settings/reset-password", label: "Reset Password", icon: ShieldCheck },
  { href: "/settings/providers", label: "Providers", icon: KeyRound },
  { href: "/settings/account", label: "Account", icon: UserCog },
] as const;

export function SettingsNav() {
  const pathname = usePathname();

  return (
    <nav className="flex gap-1 overflow-x-auto sm:w-48 sm:shrink-0 sm:flex-col sm:overflow-visible">
      {NAV_ITEMS.map((item) => {
        const active = pathname === item.href;
        const Icon = item.icon;
        return (
          <Button
            key={item.href}
            variant="ghost"
            className={cn(
              "shrink-0 justify-start gap-2",
              active && "bg-muted text-foreground"
            )}
            nativeButton={false}
            render={
              <Link href={item.href}>
                <Icon className="size-4 shrink-0" />
                {item.label}
              </Link>
            }
          />
        );
      })}
    </nav>
  );
}
