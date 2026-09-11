import { ChevronRight, CircleAlert, FileText, Loader2, OctagonX } from "lucide-react";

import { MarkdownContent } from "@/components/chat/markdown-content";
import { cn } from "cn";
import type { ChatMessage } from "@/lib/threads";

interface MessageBubbleProps {
  message: ChatMessage;
  onOpenCitations: (message: ChatMessage, activeIndex: number | null) => void;
}

export function MessageBubble({ message, onOpenCitations }: MessageBubbleProps) {
  const isUser = message.role === "user";

  return (
    <div className={cn("flex w-full", isUser ? "justify-end" : "justify-start")}>
      <div
        className={cn(
          "max-w-[85%] rounded-2xl px-4 py-2.5 sm:max-w-[70%]",
          isUser ? "bg-primary text-primary-foreground" : "bg-card"
        )}
      >
        {isUser ? (
          <p className="text-sm whitespace-pre-wrap">{message.content}</p>
        ) : message.status === "running" && !message.content ? (
          <div className="flex items-center gap-2 text-sm text-muted-foreground">
            <Loader2 className="size-3.5 animate-spin" />
            Thinking…
          </div>
        ) : (
          <>
            <MarkdownContent
              content={message.content}
              citationCount={message.chunks.length}
              onCitationClick={(index) => onOpenCitations(message, index)}
            />
            {message.status === "failed" && (
              <div className="mt-2 flex items-center gap-1.5 text-xs text-destructive">
                <CircleAlert className="size-3.5" />
                This response failed to generate.
              </div>
            )}
            {message.status === "interrupted" && (
              <div className="mt-2 flex items-center gap-1.5 text-xs text-muted-foreground">
                <OctagonX className="size-3.5" />
                Stopped before finishing.
              </div>
            )}
            {message.chunks.length > 0 && (
              <button
                type="button"
                onClick={() => onOpenCitations(message, null)}
                className="mt-2 flex items-center gap-1 border-t border-border pt-2 text-xs text-muted-foreground hover:text-foreground"
              >
                <FileText className="size-3" />
                {message.chunks.length} source{message.chunks.length === 1 ? "" : "s"}
                <ChevronRight className="size-3" />
              </button>
            )}
          </>
        )}
      </div>
    </div>
  );
}
