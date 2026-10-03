"use client";

import { useQuery } from "@tanstack/react-query";

import { getThread } from "@/lib/threads";

export function threadQueryKey(threadId: string) {
  return ["thread", threadId] as const;
}

export function useThread(threadId: string | undefined) {
  return useQuery({
    queryKey: threadQueryKey(threadId ?? ""),
    queryFn: () => getThread(threadId!),
    enabled: Boolean(threadId),
  });
}
