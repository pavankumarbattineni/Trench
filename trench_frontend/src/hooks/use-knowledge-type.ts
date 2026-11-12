"use client";

import { useCallback, useSyncExternalStore } from "react";

import { useAuth } from "@/components/auth-provider";
import type { KnowledgeType } from "@/lib/chat";

// The chat composer is recreated on every navigation (new chat, another
// thread, back from Documents/Settings), so its own useState reset the
// knowledge source to "personal" each time. The choice is kept here
// instead: per browser and per user, changed only when the user picks
// another option.
const STORAGE_PREFIX = "trench:knowledge-type:";
const listeners = new Set<() => void>();

function storageKey(userId: string | undefined): string {
  return `${STORAGE_PREFIX}${userId ?? "anonymous"}`;
}

function read(key: string): KnowledgeType {
  try {
    return localStorage.getItem(key) === "company" ? "company" : "personal";
  } catch {
    return "personal";
  }
}

function subscribe(onChange: () => void): () => void {
  listeners.add(onChange);
  // Another tab changing it keeps this tab in sync too.
  window.addEventListener("storage", onChange);
  return () => {
    listeners.delete(onChange);
    window.removeEventListener("storage", onChange);
  };
}

/** The selected knowledge source, remembered across screens and reloads.
 * A saved "company" choice shows as "personal" while the user has no
 * company access, but is kept -- it returns if access is granted again. */
export function useKnowledgeType(): [KnowledgeType, (next: KnowledgeType) => void] {
  const { user } = useAuth();
  const key = storageKey(user?.id);

  const saved = useSyncExternalStore(
    subscribe,
    () => read(key),
    () => "personal" as KnowledgeType // server render: no localStorage
  );
  const knowledgeType: KnowledgeType =
    saved === "company" && user?.has_company_access ? "company" : "personal";

  const setKnowledgeType = useCallback(
    (next: KnowledgeType) => {
      try {
        localStorage.setItem(key, next);
      } catch {
        // Best-effort: the choice just won't survive navigation.
      }
      listeners.forEach((notify) => notify());
    },
    [key]
  );

  return [knowledgeType, setKnowledgeType];
}
