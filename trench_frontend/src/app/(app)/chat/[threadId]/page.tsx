"use client";

import { use, useEffect, useState } from "react";
import Link from "next/link";
import { isAxiosError } from "axios";

import { ChatComposer } from "@/components/chat/chat-composer";
import { CitationPanel, type CitationSelection } from "@/components/chat/citation-panel";
import { MessageList } from "@/components/chat/message-list";
import { Button } from "@/components/ui/button";
import { useChatStream } from "@/hooks/use-chat-stream";
import { CHAT_COLUMN, COLUMN_GUTTER } from "@/lib/chat-layout";
import { getErrorMessage } from "@/lib/errors";
import { cn } from "cn";
import type { KnowledgeType } from "@/lib/chat";
import type { ChatMessage } from "@/lib/threads";

export default function ThreadPage({
  params,
}: {
  params: Promise<{ threadId: string }>;
}) {
  const { threadId } = use(params);
  const {
    messages,
    isLoadingThread,
    threadError,
    isStreaming,
    isSending,
    streamError,
    sendMessage,
    interrupt,
  } = useChatStream(threadId);
  const [citationSelection, setCitationSelection] = useState<CitationSelection | null>(
    null
  );

  // A source panel opened for one conversation shouldn't linger onto the
  // next one after switching threads.
  useEffect(() => {
    // eslint-disable-next-line react-hooks/set-state-in-effect -- see above
    setCitationSelection(null);
  }, [threadId]);

  const handleSend = (query: string, knowledgeType: KnowledgeType) => {
    void sendMessage(query, knowledgeType);
  };

  const handleOpenCitations = (message: ChatMessage, activeIndex: number | null) => {
    setCitationSelection({ chunks: message.chunks, activeIndex });
  };

  if (isLoadingThread) {
    return (
      <div className="flex h-full items-center justify-center">
        <p className="text-sm text-muted-foreground">Loading conversation…</p>
      </div>
    );
  }

  if (threadError) {
    const notFound = isAxiosError(threadError) && threadError.response?.status === 404;
    return (
      <div className="flex h-full flex-col items-center justify-center gap-3 text-center">
        <p className="text-sm text-muted-foreground">
          {notFound ? "This conversation doesn't exist." : getErrorMessage(threadError)}
        </p>
        <Button render={<Link href="/chat">Start a new chat</Link>} />
      </div>
    );
  }

  return (
    <div className="flex h-full">
      <div className="flex min-w-0 flex-1 flex-col">
        {/* `relative` makes this scroller the containing block for any
            absolutely-positioned content inside messages (sr-only text,
            popovers' anchors, ...), so it's clipped here instead of
            stretching the document below the viewport. */}
        <div className={cn("relative min-h-0 flex-1 overflow-y-auto", COLUMN_GUTTER)}>
          <MessageList messages={messages} onOpenCitations={handleOpenCitations} />
        </div>
        {/* Same gutter as the message scroller above (overflow-hidden just
            makes it a scroll container so scrollbar-gutter applies), so the
            composer's column lines up with the messages at every width. */}
        <div className={cn("overflow-hidden", COLUMN_GUTTER)}>
          {streamError && (
            <p className={cn(CHAT_COLUMN, "px-4 text-sm text-destructive")}>
              {streamError}
            </p>
          )}
          <ChatComposer
            isSending={isSending}
            isStreaming={isStreaming}
            onSend={handleSend}
            onStop={() => void interrupt()}
          />
        </div>
      </div>
      <CitationPanel selection={citationSelection} onClose={() => setCitationSelection(null)} />
    </div>
  );
}
