"use client";

import { useEffect, useState } from "react";

const STORAGE_KEY = "trench:sidebar-collapsed";

/** Whether the desktop sidebar is collapsed to an icon-only rail --
 * persisted per-browser so it survives a refresh/new tab, purely a
 * client-side display preference (not synced anywhere, so a read
 * failure just falls back to "expanded"). */
export function useSidebarCollapsed() {
  const [collapsed, setCollapsedState] = useState(false);
  const [hydrated, setHydrated] = useState(false);

  // Reads the persisted preference once on mount -- can't run during SSR
  // (no localStorage there), so this is a genuine "hydrate from an
  // external store" effect, not state derivable from props.
  /* eslint-disable react-hooks/set-state-in-effect */
  useEffect(() => {
    try {
      setCollapsedState(localStorage.getItem(STORAGE_KEY) === "1");
    } catch {
      // Ignore -- default to expanded.
    } finally {
      setHydrated(true);
    }
  }, []);
  /* eslint-enable react-hooks/set-state-in-effect */

  const setCollapsed = (next: boolean) => {
    setCollapsedState(next);
    try {
      localStorage.setItem(STORAGE_KEY, next ? "1" : "0");
    } catch {
      // Best-effort only -- the preference just won't survive a refresh.
    }
  };

  return { collapsed, setCollapsed, toggle: () => setCollapsed(!collapsed), hydrated };
}
