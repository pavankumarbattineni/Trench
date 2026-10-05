"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import {
  deleteCompanyDocument,
  getCompanyDownloadUrl,
  listCompanyDocuments,
  retryCompanyDocument,
  uploadCompanyDocument,
} from "@/lib/tenant-documents";

const ACTIVE_STATUSES = new Set(["pending", "processing"]);

export function companyDocumentsQueryKey(tenantId: string | undefined) {
  return ["tenant", tenantId, "documents"] as const;
}

export function useCompanyDocuments(
  tenantId: string | undefined,
  enabled: boolean
) {
  return useQuery({
    queryKey: companyDocumentsQueryKey(tenantId),
    queryFn: () => listCompanyDocuments(tenantId!),
    enabled: Boolean(tenantId) && enabled,
    // Mirrors useDocuments -- ingestion runs in the background, so poll
    // while anything is still pending/processing.
    refetchInterval: (query) =>
      query.state.data?.some((doc) => ACTIVE_STATUSES.has(doc.status))
        ? 2000
        : false,
  });
}

export function useUploadCompanyDocument(tenantId: string) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (file: File) => uploadCompanyDocument(tenantId, file),
    onSuccess: () => {
      queryClient.invalidateQueries({
        queryKey: companyDocumentsQueryKey(tenantId),
      });
    },
  });
}

export function useRetryCompanyDocument(tenantId: string) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (documentId: string) =>
      retryCompanyDocument(tenantId, documentId),
    onSuccess: () => {
      queryClient.invalidateQueries({
        queryKey: companyDocumentsQueryKey(tenantId),
      });
    },
  });
}

export function useDeleteCompanyDocument(tenantId: string) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (documentId: string) =>
      deleteCompanyDocument(tenantId, documentId),
    onSuccess: () => {
      queryClient.invalidateQueries({
        queryKey: companyDocumentsQueryKey(tenantId),
      });
    },
  });
}

// Mirrors useViewDocument/useDownloadDocument (use-documents.ts) for the
// tenant-scoped download endpoint -- a one-off action, not a cached query.
export function useViewCompanyDocument(tenantId: string) {
  return useMutation({
    mutationFn: (documentId: string) =>
      getCompanyDownloadUrl(tenantId, documentId, "inline"),
    onSuccess: ({ url }) => {
      window.open(url, "_blank", "noopener,noreferrer");
    },
  });
}

export function useDownloadCompanyDocument(tenantId: string) {
  return useMutation({
    mutationFn: (documentId: string) =>
      getCompanyDownloadUrl(tenantId, documentId, "attachment"),
    onSuccess: ({ url }) => {
      const link = document.createElement("a");
      link.href = url;
      link.download = "";
      document.body.appendChild(link);
      link.click();
      document.body.removeChild(link);
    },
  });
}
