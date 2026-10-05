import { apiClient } from "@/lib/api";
import type { MessageStatus, RetrievedChunk } from "@/lib/threads";

export type KnowledgeType = "personal" | "company";

// Mirrors ChatMessageRequest.query's max_length in app/schemas/chat.py --
// the backend rejects anything longer with a 422, so the composer should
// never let the user submit past this in the first place.
export const MAX_QUERY_LENGTH = 4000;

export interface SendMessageAccepted {
  stream_id: string;
  status: MessageStatus;
}

export async function sendChatMessage(
  threadId: string,
  query: string,
  knowledgeType: KnowledgeType
): Promise<SendMessageAccepted> {
  const { data } = await apiClient.post<SendMessageAccepted>(
    `/api/v1/chat/threads/${threadId}/messages`,
    { query, knowledge_type: knowledgeType }
  );
  return data;
}

export interface StreamStatus {
  status: MessageStatus;
  content: string;
  chunks: RetrievedChunk[];
}

export async function getStreamStatus(streamId: string): Promise<StreamStatus> {
  const { data } = await apiClient.get<StreamStatus>(
    `/api/v1/chat/streams/${streamId}/status`
  );
  return data;
}

export async function interruptStream(streamId: string): Promise<void> {
  await apiClient.delete(`/api/v1/chat/streams/${streamId}`);
}
