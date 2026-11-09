"use client";

import { useEffect, useRef } from "react";
import { MessageSquare } from "lucide-react";

import { MessageBubble } from "@/components/chat/message-bubble";
import { CHAT_COLUMN } from "@/lib/chat-layout";
import type { ChatMessage } from "@/lib/threads";
import { cn } from "cn";

/** Nearest ancestor that actually scrolls vertically. */
function scrollParent(el: HTMLElement | null): HTMLElement | null {
  for (let node = el?.parentElement; node; node = node.parentElement) {
    const { overflowY } = getComputedStyle(node);
    if (overflowY === "auto" || overflowY === "scroll") return node;
  }
  return null;
}

interface MessageListProps {
  messages: ChatMessage[];
  onOpenCitations: (message: ChatMessage, activeIndex: number | null) => void;
}

export function MessageList({ messages, onOpenCitations }: MessageListProps) {
  const endRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    // Scroll only the message list's own scroll container. scrollIntoView()
    // would also scroll every scrollable ancestor -- including the page
    // itself -- pushing the whole app up and leaving blank space below it.
    const scroller = scrollParent(endRef.current);
    scroller?.scrollTo({ top: scroller.scrollHeight, behavior: "smooth" });
  }, [messages]);

  if (messages.length === 0) {
    return (
      <div className="flex h-full flex-col items-center justify-center gap-2 text-center">
        <MessageSquare className="size-8 text-muted-foreground" />
        <p className="text-sm text-muted-foreground">
          Ask anything about your documents to get started.
        </p>
      </div>
    );
  }

  return (
    <div className={cn(CHAT_COLUMN, "flex flex-col gap-4 px-4 py-6")}>
      {messages.map((message) => (
        <MessageBubble key={message.id} message={message} onOpenCitations={onOpenCitations} />
      ))}
      <div ref={endRef} />
    </div>
  );
}
