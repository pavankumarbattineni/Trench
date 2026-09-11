"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import {
  deleteCompanyDocument,
  listCompanyDocuments,
  uploadCompanyDocument,
} from "@/lib/organization-documents";

const ACTIVE_STATUSES = new Set(["pending", "processing"]);

export function companyDocumentsQueryKey(organizationId: string | undefined) {
  return ["organization", organizationId, "documents"] as const;
}

export function useCompanyDocuments(
  organizationId: string | undefined,
  enabled: boolean
) {
  return useQuery({
    queryKey: companyDocumentsQueryKey(organizationId),
    queryFn: () => listCompanyDocuments(organizationId!),
    enabled: Boolean(organizationId) && enabled,
    // Mirrors useDocuments -- ingestion runs in the background, so poll
    // while anything is still pending/processing.
    refetchInterval: (query) =>
      query.state.data?.some((doc) => ACTIVE_STATUSES.has(doc.status))
        ? 2000
        : false,
  });
}

export function useUploadCompanyDocument(organizationId: string) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (file: File) => uploadCompanyDocument(organizationId, file),
    onSuccess: () => {
      queryClient.invalidateQueries({
        queryKey: companyDocumentsQueryKey(organizationId),
      });
    },
  });
}

export function useDeleteCompanyDocument(organizationId: string) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (documentId: string) =>
      deleteCompanyDocument(organizationId, documentId),
    onSuccess: () => {
      queryClient.invalidateQueries({
        queryKey: companyDocumentsQueryKey(organizationId),
      });
    },
  });
}
