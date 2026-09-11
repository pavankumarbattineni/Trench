"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { deleteDocument, listDocuments, uploadDocument } from "@/lib/documents";

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
