"use client";

import { useState } from "react";
import { Download, Eye, FileText, RotateCcw, Trash2 } from "lucide-react";
import { toast } from "sonner";

import { ConfirmDialog, useConfirmTarget } from "@/components/confirm-dialog";
import { useAuth } from "@/components/auth-provider";
import { DocumentStatusBadge } from "@/components/documents/document-status-badge";
import { UploadDropzone } from "@/components/documents/upload-dropzone";
import { Button } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/skeleton";
import {
  useDeleteDocument,
  useDocuments,
  useDownloadDocument,
  useRetryDocument,
  useUploadDocument,
  useViewDocument,
} from "@/hooks/use-documents";
import { getErrorMessage } from "@/lib/errors";
import { formatBytes, type DocumentSummary } from "@/lib/documents";

export default function DocumentsPage() {
  const { data: documents, isLoading, isError, error } = useDocuments();
  const { user, refreshUser } = useAuth();
  const upload = useUploadDocument();
  const remove = useDeleteDocument();
  const view = useViewDocument();
  const download = useDownloadDocument();
  const retry = useRetryDocument();
  const deleteTarget = useConfirmTarget<DocumentSummary>();
  const [uploadError, setUploadError] = useState<string | null>(null);

  const handleView = (doc: DocumentSummary) => {
    view.mutate(doc.id, {
      onError: (err) => toast.error(getErrorMessage(err)),
    });
  };

  const handleDownload = (doc: DocumentSummary) => {
    download.mutate(doc.id, {
      onError: (err) => toast.error(getErrorMessage(err)),
    });
  };

  const handleRetry = (doc: DocumentSummary) => {
    retry.mutate(doc.id, {
      onSuccess: () => toast.success(`Retrying "${doc.document_name}"…`),
      onError: (err) => toast.error(getErrorMessage(err)),
    });
  };

  const handleUpload = (file: File) => {
    setUploadError(null);
    upload.mutate(file, {
      onSuccess: () => {
        toast.success(`"${file.name}" uploaded -- processing started.`);
        // Keeps the free-tier count (used for the blur/limit UI below) in
        // sync -- the list refetch alone wouldn't update it, since it's
        // part of the user profile, not the documents response.
        void refreshUser();
      },
      onError: (err) => setUploadError(getErrorMessage(err)),
    });
  };

  const personalLimitReached =
    user != null &&
    user.personal_documents_uploaded_count >= user.personal_document_limit;

  const handleConfirmDelete = () => {
    if (!deleteTarget.target) return;
    remove.mutate(deleteTarget.target.id, {
      onSuccess: () => {
        toast.success("Document deleted.");
        deleteTarget.clear();
      },
      onError: (err) => {
        toast.error(getErrorMessage(err));
        deleteTarget.clear();
      },
    });
  };

  return (
    <main className="mx-auto flex h-full max-w-3xl flex-col gap-6 overflow-y-auto px-4 py-10">
      <div>
        <h1 className="text-2xl font-bold tracking-tight">Documents</h1>
        <p className="text-sm text-muted-foreground">
          Upload files to your personal knowledge base -- Trench uses them to answer your
          questions in chat.
        </p>
      </div>

      <UploadDropzone
        onFileSelected={handleUpload}
        disabled={upload.isPending}
        blurred={personalLimitReached}
        blurredMessage="Document limit reached"
      />
      {upload.isPending && (
        <p className="text-sm text-muted-foreground">Uploading…</p>
      )}
      {uploadError && <p className="text-sm text-destructive">{uploadError}</p>}

      <div className="flex flex-col gap-2">
        {isLoading &&
          Array.from({ length: 3 }).map((_, i) => (
            <Skeleton key={i} className="h-16 w-full rounded-xl" />
          ))}

        {isError && (
          <p className="text-sm text-destructive">{getErrorMessage(error)}</p>
        )}

        {!isLoading && documents?.length === 0 && (
          <div className="flex flex-col items-center gap-2 rounded-2xl border border-dashed border-border py-12 text-center">
            <FileText className="size-8 text-muted-foreground" />
            <p className="text-sm text-muted-foreground">
              No documents yet. Upload one to get started.
            </p>
          </div>
        )}

        {documents?.map((doc) => (
          <div
            key={doc.id}
            className="flex items-center gap-3 rounded-xl border border-border bg-card px-4 py-3"
          >
            <FileText className="size-5 shrink-0 text-muted-foreground" />
            <div className="min-w-0 flex-1">
              <p className="truncate text-sm font-medium">{doc.document_name}</p>
              <p className="text-xs text-muted-foreground">
                {formatBytes(doc.file_size)} · {doc.document_type.toUpperCase()}
                {doc.status === "completed" && ` · ${doc.chunk_count} chunks`}
              </p>
              {doc.status === "failed" && doc.error_message && (
                <p className="mt-0.5 text-xs text-destructive">{doc.error_message}</p>
              )}
            </div>
            <DocumentStatusBadge status={doc.status} />
            <Button
              variant="ghost"
              size="icon-sm"
              aria-label="View document"
              disabled={doc.status !== "completed" || (view.isPending && view.variables === doc.id)}
              onClick={() => handleView(doc)}
            >
              <Eye className="size-4" />
            </Button>
            <Button
              variant="ghost"
              size="icon-sm"
              aria-label="Download document"
              disabled={
                doc.status !== "completed" ||
                (download.isPending && download.variables === doc.id)
              }
              onClick={() => handleDownload(doc)}
            >
              <Download className="size-4" />
            </Button>
            {doc.status === "failed" && (
              <Button
                variant="ghost"
                size="icon-sm"
                aria-label="Retry processing"
                disabled={retry.isPending && retry.variables === doc.id}
                onClick={() => handleRetry(doc)}
              >
                <RotateCcw className="size-4" />
              </Button>
            )}
            <Button
              variant="ghost"
              size="icon-sm"
              aria-label="Delete document"
              disabled={doc.status === "pending" || doc.status === "processing"}
              onClick={() => deleteTarget.request(doc)}
            >
              <Trash2 className="size-4" />
            </Button>
          </div>
        ))}
      </div>

      <ConfirmDialog
        open={deleteTarget.open}
        onOpenChange={(open) => !open && deleteTarget.clear()}
        title="Delete document?"
        description={`"${deleteTarget.target?.document_name}" will be permanently removed from your knowledge base.`}
        confirmLabel="Delete"
        isPending={remove.isPending}
        onConfirm={handleConfirmDelete}
      />
    </main>
  );
}
