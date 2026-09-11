"use client";

import { use, useEffect, useState } from "react";
import Link from "next/link";
import { isAxiosError } from "axios";

import { ChatComposer } from "@/components/chat/chat-composer";
import { CitationPanel, type CitationSelection } from "@/components/chat/citation-panel";
import { MessageList } from "@/components/chat/message-list";
import { Button } from "@/components/ui/button";
import { useChatStream } from "@/hooks/use-chat-stream";
import { getErrorMessage } from "@/lib/errors";
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
        <div className="min-h-0 flex-1 overflow-y-auto">
          <MessageList messages={messages} onOpenCitations={handleOpenCitations} />
        </div>
        {streamError && (
          <p className="mx-auto w-full max-w-3xl px-4 text-sm text-destructive">
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
      <CitationPanel selection={citationSelection} onClose={() => setCitationSelection(null)} />
    </div>
  );
}
