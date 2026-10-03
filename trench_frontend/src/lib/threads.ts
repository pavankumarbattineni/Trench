import { apiClient } from "@/lib/api";

export interface ThreadSummary {
  id: string;
  title: string | null;
  created_at: string;
  updated_at: string;
}

export interface RetrievedChunk {
  chunk_id: string;
  document_id: string;
  document_name: string;
  chunk_index: number;
  content: string;
  score: number;
  // The "[n]" the model actually cited this chunk as in the response text
  // -- its 1-based position in the full candidate list the backend built
  // the prompt from, not this array's own index (chunks the model didn't
  // use are dropped before this ever reaches the frontend, so this array's
  // positions don't line up with the numbers in the text; see
  // app/graph/rag_graph.py's build_citations).
  citation_number: number;
}

export type MessageRole = "user" | "assistant";
export type MessageStatus =
  | "pending"
  | "running"
  | "completed"
  | "failed"
  | "interrupted";

export interface ChatMessage {
  id: string;
  role: MessageRole;
  content: string;
  chunks: RetrievedChunk[];
  status: MessageStatus;
  created_at: string;
}

export interface ThreadDetail {
  thread: ThreadSummary;
  messages: ChatMessage[];
}

export async function createThread(): Promise<ThreadSummary> {
  const { data } = await apiClient.post<ThreadSummary>("/api/v1/threads");
  return data;
}

export async function listThreads(): Promise<ThreadSummary[]> {
  const { data } = await apiClient.get<{ threads: ThreadSummary[] }>(
    "/api/v1/threads"
  );
  return data.threads;
}

export async function getThread(threadId: string): Promise<ThreadDetail> {
  const { data } = await apiClient.get<ThreadDetail>(
    `/api/v1/threads/${threadId}`
  );
  return data;
}

export async function deleteThread(threadId: string): Promise<void> {
  await apiClient.delete(`/api/v1/threads/${threadId}`);
}

export async function updateThreadTitle(
  threadId: string,
  title: string
): Promise<ThreadSummary> {
  const { data } = await apiClient.patch<ThreadSummary>(
    `/api/v1/threads/${threadId}`,
    { title }
  );
  return data;
}
