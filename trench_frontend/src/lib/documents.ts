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

// Every function below is personal-scoped unless `tenantId` is given, in
// which case it operates on that tenant's company documents instead --
// mirrors the backend's single /documents router (see
// app/router/documents.py), which distinguishes the two scopes the same
// way rather than exposing two separate sets of endpoints.

export async function listDocuments(tenantId?: string): Promise<DocumentSummary[]> {
  const { data } = await apiClient.get<{ documents: DocumentSummary[] }>(
    "/api/v1/documents",
    { params: tenantId ? { tenant_id: tenantId } : undefined }
  );
  return data.documents;
}

export async function uploadDocument(
  file: File,
  tenantId?: string
): Promise<DocumentSummary> {
  const form = new FormData();
  form.append("file", file);
  if (tenantId) form.append("tenant_id", tenantId);
  const { data } = await apiClient.post<DocumentSummary>("/api/v1/documents", form);
  return data;
}

export async function deleteDocument(
  documentId: string,
  tenantId?: string
): Promise<void> {
  await apiClient.delete(`/api/v1/documents/${documentId}`, {
    params: tenantId ? { tenant_id: tenantId } : undefined,
  });
}

export async function retryDocument(
  documentId: string,
  tenantId?: string
): Promise<DocumentSummary> {
  const form = new FormData();
  form.append("document_id", documentId);
  form.append("retry", "true");
  if (tenantId) form.append("tenant_id", tenantId);
  const { data } = await apiClient.post<DocumentSummary>("/api/v1/documents", form);
  return data;
}

export interface DownloadUrlResponse {
  url: string;
  expires_in: number;
}

export async function getDownloadUrl(
  documentId: string,
  disposition: "inline" | "attachment",
  tenantId?: string
): Promise<DownloadUrlResponse> {
  const { data } = await apiClient.get<DownloadUrlResponse>(
    `/api/v1/documents/${documentId}/download`,
    { params: { disposition, ...(tenantId ? { tenant_id: tenantId } : {}) } }
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
