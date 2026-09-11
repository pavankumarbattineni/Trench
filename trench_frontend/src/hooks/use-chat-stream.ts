"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { useQueryClient } from "@tanstack/react-query";

import { openChatStream, type SseFrame } from "@/lib/chat-stream";
import {
  interruptStream,
  sendChatMessage,
  type KnowledgeType,
} from "@/lib/chat";
import { threadQueryKey, useThread } from "@/hooks/use-thread";
import { threadsQueryKey } from "@/hooks/use-threads";
import type { ChatMessage, ThreadDetail } from "@/lib/threads";

interface TokenFrame {
  content: string;
}
interface DoneFrame {
  content: string;
  citations: ChatMessage["chunks"];
}
interface ErrorFrame {
  content: string;
}
interface InterruptedFrame {
  content: string;
}

/** Drives one thread's chat turn: sending a message, consuming its SSE
 * stream token-by-token, and reconciling the result into the shared
 * react-query cache that `useThread` reads -- so the message list, the
 * streaming bubble, and a page refresh mid-generation all stay consistent
 * without three separate sources of truth. */
export function useChatStream(threadId: string) {
  const queryClient = useQueryClient();
  const threadQuery = useThread(threadId);

  const [isStreaming, setIsStreaming] = useState(false);
  const [streamingContent, setStreamingContent] = useState("");
  const [streamError, setStreamError] = useState<string | null>(null);
  const [isSending, setIsSending] = useState(false);
  // Mirrors activeStreamIdRef for rendering -- state updates trigger a
  // re-render, a ref read during render would not (and is disallowed).
  const [activeStreamId, setActiveStreamId] = useState<string | null>(null);

  const abortControllerRef = useRef<AbortController | null>(null);
  // The source of truth read inside async callbacks (e.g. consumeStream's
  // `finally`) to check "is this still the stream we care about" -- state
  // read in a closure would be stale by the time that callback runs.
  const activeStreamIdRef = useRef<string | null>(null);
  const contentRef = useRef("");
  const resumedThreadsRef = useRef<Set<string>>(new Set());

  // Resets in-flight stream state when switching threads -- otherwise the
  // previous thread's streaming bubble would flash onto the newly-opened
  // one for a frame before this runs. A real side effect (aborting the
  // in-flight fetch, resetting local-only state tied to *which stream*,
  // not to any server data), not something derivable from props/query
  // data, hence the effect rather than deriving it during render.
  /* eslint-disable react-hooks/set-state-in-effect -- see above */
  useEffect(() => {
    abortControllerRef.current?.abort();
    abortControllerRef.current = null;
    activeStreamIdRef.current = null;
    contentRef.current = "";
    setActiveStreamId(null);
    setIsStreaming(false);
    setStreamingContent("");
    setStreamError(null);
  }, [threadId]);
  /* eslint-enable react-hooks/set-state-in-effect */

  useEffect(() => {
    return () => abortControllerRef.current?.abort();
  }, []);

  const upsertMessage = useCallback(
    (message: ChatMessage) => {
      queryClient.setQueryData<ThreadDetail | undefined>(
        threadQueryKey(threadId),
        (prev) => {
          if (!prev) return prev;
          const withoutStreaming = prev.messages.filter(
            (existing) => existing.id !== message.id
          );
          return { ...prev, messages: [...withoutStreaming, message] };
        }
      );
    },
    [queryClient, threadId]
  );

  const consumeStream = useCallback(
    async (streamId: string) => {
      activeStreamIdRef.current = streamId;
      contentRef.current = "";
      setActiveStreamId(streamId);
      setStreamingContent("");
      setStreamError(null);
      setIsStreaming(true);

      const controller = new AbortController();
      abortControllerRef.current = controller;

      try {
        await openChatStream(streamId, {
          signal: controller.signal,
          onFrame: (frame: SseFrame) => {
            switch (frame.event) {
              case "token": {
                contentRef.current += (frame.data as TokenFrame).content;
                setStreamingContent(contentRef.current);
                break;
              }
              case "done": {
                const data = frame.data as DoneFrame;
                upsertMessage({
                  id: streamId,
                  role: "assistant",
                  content: data.content,
                  chunks: data.citations ?? [],
                  status: "completed",
                  created_at: new Date().toISOString(),
                });
                break;
              }
              case "error": {
                const data = frame.data as ErrorFrame;
                setStreamError(
                  data.content || "Something went wrong while generating a response."
                );
                upsertMessage({
                  id: streamId,
                  role: "assistant",
                  content: data.content,
                  chunks: [],
                  status: "failed",
                  created_at: new Date().toISOString(),
                });
                break;
              }
              case "interrupted": {
                const data = frame.data as InterruptedFrame;
                upsertMessage({
                  id: streamId,
                  role: "assistant",
                  content: data.content,
                  chunks: [],
                  status: "interrupted",
                  created_at: new Date().toISOString(),
                });
                break;
              }
            }
          },
        });
      } catch (err) {
        if ((err as Error).name !== "AbortError") {
          setStreamError("Lost connection to the response stream.");
        }
      } finally {
        if (activeStreamIdRef.current === streamId) {
          setIsStreaming(false);
          setActiveStreamId(null);
          activeStreamIdRef.current = null;
        }
        abortControllerRef.current = null;
        queryClient.invalidateQueries({ queryKey: threadsQueryKey });
      }
    },
    [upsertMessage, queryClient]
  );

  // A page refresh (or a tab that was never listening) can land on a
  // thread whose last message is still pending/running -- the generation
  // itself is a server-side background task independent of any open SSE
  // connection, so it may already be done or still going. Reconnecting
  // from scratch (no Last-Event-ID) is correct either way: the backend's
  // buffer replays every token emitted since the turn started. This is a
  // genuine "fetch on data becoming available" effect, not a derived-state
  // case, so the setState calls inside consumeStream are intentional.
  useEffect(() => {
    const detail = threadQuery.data;
    if (!detail || resumedThreadsRef.current.has(threadId)) return;
    const last = detail.messages.at(-1);
    if (last && last.role === "assistant" && ["pending", "running"].includes(last.status)) {
      resumedThreadsRef.current.add(threadId);
      // eslint-disable-next-line react-hooks/set-state-in-effect -- see above
      void consumeStream(last.id);
    }
  }, [threadQuery.data, threadId, consumeStream]);

  const sendMessage = useCallback(
    async (query: string, knowledgeType: KnowledgeType) => {
      setIsSending(true);
      setStreamError(null);
      const optimisticUserMessage: ChatMessage = {
        id: `pending-user-${Date.now()}`,
        role: "user",
        content: query,
        chunks: [],
        status: "completed",
        created_at: new Date().toISOString(),
      };
      queryClient.setQueryData<ThreadDetail | undefined>(
        threadQueryKey(threadId),
        (prev) =>
          prev ? { ...prev, messages: [...prev.messages, optimisticUserMessage] } : prev
      );

      try {
        const accepted = await sendChatMessage(threadId, query, knowledgeType);
        await consumeStream(accepted.stream_id);
      } finally {
        setIsSending(false);
      }
    },
    [threadId, queryClient, consumeStream]
  );

  const interrupt = useCallback(async () => {
    const streamId = activeStreamIdRef.current;
    if (!streamId) return;
    // Local state updates immediately -- the user shouldn't wait on a
    // round trip to see the "stop" take effect.
    abortControllerRef.current?.abort();
    abortControllerRef.current = null;
    activeStreamIdRef.current = null;
    setActiveStreamId(null);
    setIsStreaming(false);
    upsertMessage({
      id: streamId,
      role: "assistant",
      content: contentRef.current,
      chunks: [],
      status: "interrupted",
      created_at: new Date().toISOString(),
    });
    try {
      await interruptStream(streamId);
    } catch {
      // Best-effort: the server-side task may already have finished: the
      // local "interrupted" bubble stands regardless.
    }
  }, [upsertMessage]);

  const messages = threadQuery.data?.messages ?? [];
  const hasActiveMessage = messages.some((m) => m.id === activeStreamId);
  const displayMessages =
    isStreaming && activeStreamId && !hasActiveMessage
      ? [
          ...messages,
          {
            id: activeStreamId,
            role: "assistant" as const,
            content: streamingContent,
            chunks: [],
            status: "running" as const,
            created_at: new Date().toISOString(),
          },
        ]
      : messages;

  return {
    thread: threadQuery.data?.thread,
    messages: displayMessages,
    isLoadingThread: threadQuery.isLoading,
    threadError: threadQuery.error,
    isStreaming,
    isSending,
    streamError,
    sendMessage,
    interrupt,
  };
}
