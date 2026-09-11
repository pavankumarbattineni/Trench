"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { threadQueryKey } from "@/hooks/use-thread";
import {
  createThread,
  deleteThread,
  listThreads,
  updateThreadTitle,
} from "@/lib/threads";

export const threadsQueryKey = ["threads"] as const;

export function useThreads() {
  return useQuery({
    queryKey: threadsQueryKey,
    queryFn: listThreads,
    // Auto-generated titles land a few seconds after the first message;
    // this keeps the sidebar's thread names fresh without the caller
    // having to know when generation finished.
    refetchInterval: (query) =>
      query.state.data?.some((thread) => thread.title === null) ? 3000 : false,
  });
}

export function useCreateThread() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: createThread,
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: threadsQueryKey });
    },
  });
}

export function useDeleteThread() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: deleteThread,
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: threadsQueryKey });
    },
  });
}

export function useRenameThread(threadId: string) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (title: string) => updateThreadTitle(threadId, title),
    onSuccess: (updated) => {
      queryClient.invalidateQueries({ queryKey: threadsQueryKey });
      queryClient.setQueryData(threadQueryKey(threadId), (prev: unknown) => {
        if (!prev || typeof prev !== "object" || !("thread" in prev)) return prev;
        return { ...prev, thread: updated };
      });
    },
  });
}
