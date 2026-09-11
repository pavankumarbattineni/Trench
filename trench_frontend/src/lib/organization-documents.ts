import { apiClient } from "@/lib/api";
import type { DocumentSummary } from "@/lib/documents";

export async function listCompanyDocuments(
  organizationId: string
): Promise<DocumentSummary[]> {
  const { data } = await apiClient.get<{ documents: DocumentSummary[] }>(
    `/api/v1/organizations/${organizationId}/documents`
  );
  return data.documents;
}

export async function uploadCompanyDocument(
  organizationId: string,
  file: File
): Promise<DocumentSummary> {
  const form = new FormData();
  form.append("file", file);
  const { data } = await apiClient.post<DocumentSummary>(
    `/api/v1/organizations/${organizationId}/documents`,
    form
  );
  return data;
}

export async function deleteCompanyDocument(
  organizationId: string,
  documentId: string
): Promise<void> {
  await apiClient.delete(
    `/api/v1/organizations/${organizationId}/documents/${documentId}`
  );
}
