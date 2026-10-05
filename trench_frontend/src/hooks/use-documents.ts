"use client";

import { useEffect, useMemo, useRef } from "react";
import {
  useMutation,
  useQueries,
  useQuery,
  useQueryClient,
} from "@tanstack/react-query";

import {
  deleteDocument,
  getDocumentStatus,
  getDownloadUrl,
  listDocuments,
  retryDocument,
  uploadDocument,
  type DocumentSummary,
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

// Ingestion (parse -> chunk -> embed -> index) runs in the background and
// can take tens of seconds. Rather than re-fetching the whole list every
// 2s (GET /documents, same cost regardless of how many rows changed),
// poll GET /documents/{id}/status per pending/processing document --
// much cheaper per tick, since it reads one row instead of a full scope
// scan. An in-flight status change (pending -> processing) patches the
// cached list in place; reaching a terminal status (completed/failed)
// triggers exactly one full list re-fetch, since the status-only
// response doesn't carry chunk_count/error_message/processed_at.
function useDocumentStatusPolling(
  documents: DocumentSummary[] | undefined,
  tenantId: string | undefined
): void {
  const queryClient = useQueryClient();
  const notifiedRef = useRef<Set<string>>(new Set());

  const activeDocs = useMemo(
    () => (documents ?? []).filter((doc) => ACTIVE_STATUSES.has(doc.status)),
    [documents]
  );

  useEffect(() => {
    const activeIds = new Set(activeDocs.map((doc) => doc.id));
    for (const id of notifiedRef.current) {
      if (!activeIds.has(id)) notifiedRef.current.delete(id);
    }
  }, [activeDocs]);

  const statusQueries = useQueries({
    queries: activeDocs.map((doc) => ({
      queryKey: ["document-status", doc.id] as const,
      queryFn: () => getDocumentStatus(doc.id),
      refetchInterval: 2000,
    })),
  });

  useEffect(() => {
    statusQueries.forEach((query, i) => {
      const doc = activeDocs[i];
      const newStatus = query.data?.status;
      if (!doc || !newStatus || newStatus === doc.status) return;

      if (ACTIVE_STATUSES.has(newStatus)) {
        queryClient.setQueryData<DocumentSummary[]>(
          documentsQueryKey(tenantId),
          (current) =>
            current?.map((d) =>
              d.id === doc.id ? { ...d, status: newStatus } : d
            )
        );
      } else if (!notifiedRef.current.has(doc.id)) {
        notifiedRef.current.add(doc.id);
        queryClient.invalidateQueries({ queryKey: documentsQueryKey(tenantId) });
      }
    });
  }, [statusQueries, activeDocs, queryClient, tenantId]);
}

export function useDocuments(tenantId?: string, enabled = true) {
  const query = useQuery({
    queryKey: documentsQueryKey(tenantId),
    queryFn: () => listDocuments(tenantId),
    enabled,
  });

  useDocumentStatusPolling(query.data, tenantId);

  return query;
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
      // Retry resets the document to "pending", so once this invalidation
      // refetches the list, useDocumentStatusPolling picks it back up
      // automatically (it polls whatever's pending/processing in the
      // latest list data) -- no extra polling logic needed here.
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
