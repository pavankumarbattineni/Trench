"use client";

import { useState } from "react";
import { useRouter } from "next/navigation";
import { toast } from "sonner";

import { ChatComposer } from "@/components/chat/chat-composer";
import { useCreateThread } from "@/hooks/use-threads";
import { sendChatMessage, type KnowledgeType } from "@/lib/chat";
import { getErrorMessage } from "@/lib/errors";

export default function NewChatPage() {
  const router = useRouter();
  const createThread = useCreateThread();
  const [isSending, setIsSending] = useState(false);

  const handleSend = async (query: string, knowledgeType: KnowledgeType) => {
    setIsSending(true);
    try {
      const thread = await createThread.mutateAsync();
      // The assistant's generation keeps running server-side regardless of
      // which page is open -- the thread page picks it up on mount via
      // useChatStream's resume-if-running check, so there's no need to
      // consume the stream here before navigating.
      await sendChatMessage(thread.id, query, knowledgeType);
      router.push(`/chat/${thread.id}`);
    } catch (err) {
      toast.error(getErrorMessage(err));
      setIsSending(false);
    }
  };

  return (
    <div className="flex h-full flex-col">
      <div className="flex flex-1 flex-col items-center justify-center gap-2 px-4 text-center">
        <h1 className="text-xl font-semibold tracking-tight">What can I help with?</h1>
        <p className="text-sm text-muted-foreground">
          Ask a question about your documents to get started.
        </p>
      </div>
      <ChatComposer isSending={isSending} isStreaming={false} onSend={handleSend} onStop={() => {}} />
    </div>
  );
}
