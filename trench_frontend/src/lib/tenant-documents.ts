import { apiClient } from "@/lib/api";
import type { DocumentSummary, DownloadUrlResponse } from "@/lib/documents";

export async function listCompanyDocuments(
  tenantId: string
): Promise<DocumentSummary[]> {
  const { data } = await apiClient.get<{ documents: DocumentSummary[] }>(
    `/api/v1/tenants/${tenantId}/documents`
  );
  return data.documents;
}

export async function uploadCompanyDocument(
  tenantId: string,
  file: File
): Promise<DocumentSummary> {
  const form = new FormData();
  form.append("file", file);
  const { data } = await apiClient.post<DocumentSummary>(
    `/api/v1/tenants/${tenantId}/documents`,
    form
  );
  return data;
}

export async function retryCompanyDocument(
  tenantId: string,
  documentId: string
): Promise<DocumentSummary> {
  const form = new FormData();
  form.append("document_id", documentId);
  form.append("retry", "true");
  const { data } = await apiClient.post<DocumentSummary>(
    `/api/v1/tenants/${tenantId}/documents`,
    form
  );
  return data;
}

export async function deleteCompanyDocument(
  tenantId: string,
  documentId: string
): Promise<void> {
  await apiClient.delete(
    `/api/v1/tenants/${tenantId}/documents/${documentId}`
  );
}

export async function getCompanyDownloadUrl(
  tenantId: string,
  documentId: string,
  disposition: "inline" | "attachment"
): Promise<DownloadUrlResponse> {
  const { data } = await apiClient.get<DownloadUrlResponse>(
    `/api/v1/tenants/${tenantId}/documents/${documentId}/download`,
    { params: { disposition } }
  );
  return data;
}
