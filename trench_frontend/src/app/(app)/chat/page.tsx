"use client";

import { useState } from "react";
import { useRouter } from "next/navigation";
import { toast } from "sonner";

import { ChatComposer } from "@/components/chat/chat-composer";
import { useCreateThread } from "@/hooks/use-threads";
import { sendChatMessage, type KnowledgeType } from "@/lib/chat";
import { getErrorMessage } from "@/lib/errors";

const NEW_CHAT_MESSAGES = [
  "Your knowledge, unleashed.",
  "Turn knowledge into action.",
  "Your data. Your context. Your AI.",
] as const;

export default function NewChatPage() {
  const router = useRouter();
  const createThread = useCreateThread();
  const [isSending, setIsSending] = useState(false);
  const [welcomeMessage] = useState(
    () => NEW_CHAT_MESSAGES[Math.floor(Math.random() * NEW_CHAT_MESSAGES.length)]
  );

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
    <div className="relative flex h-full flex-col">
      <div className="absolute inset-x-0 top-1/2 flex -translate-y-1/2 flex-col items-center text-center transition-[top,transform] duration-300 ease-out">
        <div className="-mb-2 px-4">
          <h1 className="text-[23px] font-medium tracking-tight">{welcomeMessage}</h1>
        </div>
        <div className="w-full">
          <ChatComposer
            isSending={isSending}
            isStreaming={false}
            onSend={handleSend}
            onStop={() => {}}
          />
        </div>
      </div>
    </div>
  );
}
