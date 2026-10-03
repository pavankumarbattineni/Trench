"use client";

import { useEffect, useRef } from "react";
import { MessageSquare } from "lucide-react";

import { MessageBubble } from "@/components/chat/message-bubble";
import type { ChatMessage } from "@/lib/threads";

interface MessageListProps {
  messages: ChatMessage[];
  onOpenCitations: (message: ChatMessage, activeIndex: number | null) => void;
}

export function MessageList({ messages, onOpenCitations }: MessageListProps) {
  const endRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    endRef.current?.scrollIntoView({ behavior: "smooth" });
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
    <div className="mx-auto flex w-full max-w-3xl flex-col gap-4 px-4 py-6">
      {messages.map((message) => (
        <MessageBubble key={message.id} message={message} onOpenCitations={onOpenCitations} />
      ))}
      <div ref={endRef} />
    </div>
  );
}
