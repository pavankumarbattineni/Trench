"use client";

import { useSyncExternalStore } from "react";

/**
 * The current time, shared by every relative timestamp on the page and
 * refreshed every 30 seconds -- so "Just now" becomes "1 min ago" and a
 * label switches to a date right after midnight, without each timestamp
 * running its own timer. The interval only runs while something is
 * subscribed.
 */

const TICK_MS = 30_000;

let now = Date.now();
const listeners = new Set<() => void>();
let timer: ReturnType<typeof setInterval> | null = null;

function subscribe(listener: () => void): () => void {
  listeners.add(listener);
  if (timer === null) {
    now = Date.now();
    timer = setInterval(() => {
      now = Date.now();
      listeners.forEach((notify) => notify());
    }, TICK_MS);
  }
  return () => {
    listeners.delete(listener);
    if (listeners.size === 0 && timer !== null) {
      clearInterval(timer);
      timer = null;
    }
  };
}

const getSnapshot = () => now;

export function useNow(): Date {
  const timestamp = useSyncExternalStore(subscribe, getSnapshot, getSnapshot);
  return new Date(timestamp);
}
