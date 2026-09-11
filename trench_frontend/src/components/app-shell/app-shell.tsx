"use client";

import { useState } from "react";
import { Menu } from "lucide-react";

import { AppSidebarContent } from "@/components/app-shell/sidebar-content";
import { Button } from "@/components/ui/button";
import { Sheet, SheetContent, SheetTitle } from "@/components/ui/sheet";
import { useSidebarCollapsed } from "@/hooks/use-sidebar-state";
import { cn } from "cn";

export function AppShell({ children }: { children: React.ReactNode }) {
  const [mobileOpen, setMobileOpen] = useState(false);
  const { collapsed, toggle, hydrated } = useSidebarCollapsed();

  return (
    <div className="flex h-dvh bg-background text-foreground">
      <aside
        className={cn(
          "hidden shrink-0 border-r border-border md:flex",
          collapsed ? "w-16" : "w-64",
          // Skip the width transition on first paint so the persisted
          // preference doesn't visibly animate in on every load.
          hydrated && "transition-[width] duration-200"
        )}
      >
        <AppSidebarContent collapsed={collapsed} onToggleCollapse={toggle} />
      </aside>

      <Sheet open={mobileOpen} onOpenChange={setMobileOpen}>
        <SheetContent side="left" className="w-72 p-0">
          <SheetTitle className="sr-only">Navigation</SheetTitle>
          <AppSidebarContent onNavigate={() => setMobileOpen(false)} />
        </SheetContent>
      </Sheet>

      <div className="flex min-w-0 flex-1 flex-col">
        <header className="flex items-center border-b border-border p-2 md:hidden">
          <Button
            variant="ghost"
            size="icon"
            aria-label="Open navigation"
            onClick={() => setMobileOpen(true)}
          >
            <Menu className="size-5" />
          </Button>
        </header>
        <div className="min-h-0 flex-1">{children}</div>
      </div>
    </div>
  );
}
