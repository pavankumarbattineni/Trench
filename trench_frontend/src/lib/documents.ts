import { apiClient } from "@/lib/api";

export type DocumentStatus = "pending" | "processing" | "completed" | "failed";
export type DocumentType = "pdf" | "docx" | "txt" | "md";

export interface DocumentSummary {
  id: string;
  document_name: string;
  document_type: DocumentType;
  knowledge_type: "personal" | "company";
  knowledge_base: string;
  file_size: number;
  status: DocumentStatus;
  error_message: string | null;
  chunk_count: number;
  created_at: string;
  processed_at: string | null;
}

export async function listDocuments(): Promise<DocumentSummary[]> {
  const { data } = await apiClient.get<{ documents: DocumentSummary[] }>(
    "/api/v1/documents"
  );
  return data.documents;
}

export async function getDocument(documentId: string): Promise<DocumentSummary> {
  const { data } = await apiClient.get<DocumentSummary>(
    `/api/v1/documents/${documentId}`
  );
  return data;
}

export async function uploadDocument(file: File): Promise<DocumentSummary> {
  const form = new FormData();
  form.append("file", file);
  const { data } = await apiClient.post<DocumentSummary>(
    "/api/v1/documents",
    form
  );
  return data;
}

export async function deleteDocument(documentId: string): Promise<void> {
  await apiClient.delete(`/api/v1/documents/${documentId}`);
}

export interface DownloadUrlResponse {
  url: string;
  expires_in: number;
}

export async function getDownloadUrl(
  documentId: string,
  disposition: "inline" | "attachment"
): Promise<DownloadUrlResponse> {
  const { data } = await apiClient.get<DownloadUrlResponse>(
    `/api/v1/documents/${documentId}/download`,
    { params: { disposition } }
  );
  return data;
}

export function formatBytes(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}

export const MAX_DOCUMENT_SIZE_BYTES = 20 * 1024 * 1024;
export const ACCEPTED_DOCUMENT_TYPES = [
  "application/pdf",
  "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
  "text/plain",
  "text/markdown",
] as const;
export const ACCEPTED_DOCUMENT_EXTENSIONS = [".pdf", ".docx", ".txt", ".md"];
