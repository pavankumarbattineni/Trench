"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import {
  deleteDocument,
  getDownloadUrl,
  listDocuments,
  retryDocument,
  uploadDocument,
} from "@/lib/documents";

// Personal and company documents are the same backend scope, keyed apart
// here only so switching between "my documents" and "this tenant's
// documents" doesn't show one scope's cached list while the other is
// loading (see lib/documents.ts -- every call is personal-scoped unless
// `tenantId` is given).
export function documentsQueryKey(tenantId?: string) {
  return ["documents", tenantId ?? null] as const;
}

const ACTIVE_STATUSES = new Set(["pending", "processing"]);

export function useDocuments(tenantId?: string, enabled = true) {
  return useQuery({
    queryKey: documentsQueryKey(tenantId),
    queryFn: () => listDocuments(tenantId),
    enabled,
    // Ingestion (parse -> chunk -> embed -> index) runs in the background
    // and can take tens of seconds -- poll while anything is still
    // pending/processing so status/chunk_count update without a manual
    // refresh, and stop once nothing is in flight.
    refetchInterval: (query) =>
      query.state.data?.some((doc) => ACTIVE_STATUSES.has(doc.status))
        ? 2000
        : false,
  });
}

export function useUploadDocument(tenantId?: string) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (file: File) => uploadDocument(file, tenantId),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: documentsQueryKey(tenantId) });
    },
  });
}

export function useDeleteDocument(tenantId?: string) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (documentId: string) => deleteDocument(documentId, tenantId),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: documentsQueryKey(tenantId) });
    },
  });
}

export function useRetryDocument(tenantId?: string) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (documentId: string) => retryDocument(documentId, tenantId),
    onSuccess: () => {
      // Retry resets the document to "pending", so useDocuments' own
      // refetchInterval picks the poll back up automatically once this
      // invalidation refetches the list -- no extra polling logic needed.
      queryClient.invalidateQueries({ queryKey: documentsQueryKey(tenantId) });
    },
  });
}

// One-off action, not a cached query -- fetches a fresh short-lived
// presigned URL on every call (it expires in 300s, so there's nothing
// worth caching) and immediately opens it in a new tab.
export function useViewDocument(tenantId?: string) {
  return useMutation({
    mutationFn: (documentId: string) =>
      getDownloadUrl(documentId, "inline", tenantId),
    onSuccess: ({ url }) => {
      window.open(url, "_blank", "noopener,noreferrer");
    },
  });
}

// Same idea as useViewDocument, but forces a save-to-disk via a throwaway
// <a download> element instead of opening the URL in a new tab.
export function useDownloadDocument(tenantId?: string) {
  return useMutation({
    mutationFn: (documentId: string) =>
      getDownloadUrl(documentId, "attachment", tenantId),
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
