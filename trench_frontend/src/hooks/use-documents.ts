"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import {
  deleteDocument,
  getDownloadUrl,
  listDocuments,
  uploadDocument,
} from "@/lib/documents";

export const documentsQueryKey = ["documents"] as const;

const ACTIVE_STATUSES = new Set(["pending", "processing"]);

export function useDocuments() {
  return useQuery({
    queryKey: documentsQueryKey,
    queryFn: listDocuments,
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

export function useUploadDocument() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: uploadDocument,
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: documentsQueryKey });
    },
  });
}

export function useDeleteDocument() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: deleteDocument,
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: documentsQueryKey });
    },
  });
}

// One-off action, not a cached query -- fetches a fresh short-lived
// presigned URL on every call (it expires in 300s, so there's nothing
// worth caching) and immediately opens it in a new tab.
export function useViewDocument() {
  return useMutation({
    mutationFn: (documentId: string) => getDownloadUrl(documentId, "inline"),
    onSuccess: ({ url }) => {
      window.open(url, "_blank", "noopener,noreferrer");
    },
  });
}

// Same idea as useViewDocument, but forces a save-to-disk via a throwaway
// <a download> element instead of opening the URL in a new tab.
export function useDownloadDocument() {
  return useMutation({
    mutationFn: (documentId: string) =>
      getDownloadUrl(documentId, "attachment"),
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
