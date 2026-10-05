import { useState } from "react";
import {
  ChevronDown,
  ChevronRight,
  ChevronUp,
  CircleAlert,
  FileText,
  Loader2,
  OctagonX,
} from "lucide-react";

import { MarkdownContent } from "@/components/chat/markdown-content";
import { cn } from "cn";
import type { ChatMessage } from "@/lib/threads";

interface MessageBubbleProps {
  message: ChatMessage;
  onOpenCitations: (message: ChatMessage, activeIndex: number | null) => void;
}

// Past this many characters, a user message collapses by default (behind
// an expand/collapse toggle) so one long paste doesn't push the rest of
// the conversation out of view. Well under the backend's 4000-char query
// cap (app/schemas/chat.py) -- this is purely a display threshold, not a
// validation one.
const USER_MESSAGE_COLLAPSE_THRESHOLD = 400;

function UserMessageContent({ content }: { content: string }) {
  const [isExpanded, setIsExpanded] = useState(false);
  const isLong = content.length > USER_MESSAGE_COLLAPSE_THRESHOLD;

  if (!isLong) {
    return <p className="text-sm whitespace-pre-wrap">{content}</p>;
  }

  return (
    <div>
      <p
        className={cn(
          "text-sm whitespace-pre-wrap",
          !isExpanded && "line-clamp-4"
        )}
      >
        {content}
      </p>
      <button
        type="button"
        onClick={() => setIsExpanded((expanded) => !expanded)}
        className="mt-1.5 flex items-center gap-1 text-xs text-primary-foreground/80 hover:text-primary-foreground"
      >
        {isExpanded ? (
          <>
            Show less
            <ChevronUp className="size-3" />
          </>
        ) : (
          <>
            Show more
            <ChevronDown className="size-3" />
          </>
        )}
      </button>
    </div>
  );
}

export function MessageBubble({ message, onOpenCitations }: MessageBubbleProps) {
  const isUser = message.role === "user";

  return (
    <div className={cn("flex w-full", isUser ? "justify-end" : "justify-start")}>
      <div
        className={cn(
          "rounded-2xl px-4 py-2.5",
          isUser
            ? "max-w-[85%] bg-primary text-primary-foreground sm:max-w-[70%]"
            // No max-width cap here -- the assistant bubble fills the
            // chat column's full width, which is exactly where every
            // user bubble's right edge lands too (it's right-aligned via
            // `justify-end` above, flush against that same boundary
            // regardless of its own text length).
            : "w-full bg-card"
        )}
      >
        {isUser ? (
          <UserMessageContent content={message.content} />
        ) : message.status === "running" && !message.content ? (
          <div className="flex items-center gap-2 text-sm text-muted-foreground">
            <Loader2 className="size-3.5 animate-spin" />
            Thinking…
          </div>
        ) : (
          <>
            <MarkdownContent
              content={message.content}
              citationNumbers={
                new Set(message.chunks.map((chunk) => chunk.citation_number))
              }
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
                <i>Trench Was Interrupted.</i>
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
