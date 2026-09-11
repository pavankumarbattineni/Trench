"use client";

import { useState } from "react";
import { FileText, ShieldAlert, Trash2 } from "lucide-react";
import { toast } from "sonner";

import { ConfirmDialog, useConfirmTarget } from "@/components/confirm-dialog";
import { DocumentStatusBadge } from "@/components/documents/document-status-badge";
import { UploadDropzone } from "@/components/documents/upload-dropzone";
import { Button } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/skeleton";
import {
  useCompanyDocuments,
  useDeleteCompanyDocument,
  useUploadCompanyDocument,
} from "@/hooks/use-organization-documents";
import { getErrorMessage } from "@/lib/errors";
import type { DocumentSummary } from "@/lib/documents";

function formatBytes(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}

interface OrganizationDocumentsPanelProps {
  organizationId: string;
  /** May upload/delete -- admin only, no delegation to members. */
  canManage: boolean;
  /** May at least see the list -- an admin, or a member with company-knowledge (query) access. */
  canView: boolean;
}

export function OrganizationDocumentsPanel({
  organizationId,
  canManage,
  canView,
}: OrganizationDocumentsPanelProps) {
  const { data: documents, isLoading, isError, error } = useCompanyDocuments(
    organizationId,
    canView
  );
  const upload = useUploadCompanyDocument(organizationId);
  const remove = useDeleteCompanyDocument(organizationId);
  const deleteTarget = useConfirmTarget<DocumentSummary>();
  const [uploadError, setUploadError] = useState<string | null>(null);

  const handleUpload = (file: File) => {
    setUploadError(null);
    upload.mutate(file, {
      onSuccess: () => toast.success(`"${file.name}" uploaded -- processing started.`),
      onError: (err) => setUploadError(getErrorMessage(err)),
    });
  };

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

  if (!canView) {
    return (
      <div className="flex flex-col items-center gap-2 rounded-2xl border border-dashed border-border py-12 text-center">
        <ShieldAlert className="size-8 text-muted-foreground" />
        <p className="text-sm text-muted-foreground">
          You don&apos;t have access to this organization&apos;s company documents. Ask
          an admin to grant you company-knowledge access.
        </p>
      </div>
    );
  }

  return (
    <div className="flex flex-col gap-4">
      <p className="text-sm text-muted-foreground">
        {canManage
          ? "Documents uploaded here are shared with every member who has company-knowledge access."
          : "Documents your organization has shared as company knowledge."}
      </p>

      {canManage && (
        <>
          <UploadDropzone onFileSelected={handleUpload} disabled={upload.isPending} />
          {upload.isPending && (
            <p className="text-sm text-muted-foreground">Uploading…</p>
          )}
          {uploadError && <p className="text-sm text-destructive">{uploadError}</p>}
        </>
      )}

      <div className="flex flex-col gap-2">
        {isLoading &&
          Array.from({ length: 3 }).map((_, i) => (
            <Skeleton key={i} className="h-16 w-full rounded-xl" />
          ))}

        {isError && <p className="text-sm text-destructive">{getErrorMessage(error)}</p>}

        {!isLoading && documents?.length === 0 && (
          <div className="flex flex-col items-center gap-2 rounded-2xl border border-dashed border-border py-12 text-center">
            <FileText className="size-8 text-muted-foreground" />
            <p className="text-sm text-muted-foreground">
              No company documents yet.{" "}
              {canManage ? "Upload one to get started." : "Check back later."}
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
            {canManage && (
              <Button
                variant="ghost"
                size="icon-sm"
                aria-label="Delete document"
                disabled={doc.status === "pending" || doc.status === "processing"}
                onClick={() => deleteTarget.request(doc)}
              >
                <Trash2 className="size-4" />
              </Button>
            )}
          </div>
        ))}
      </div>

      <ConfirmDialog
        open={deleteTarget.open}
        onOpenChange={(open) => !open && deleteTarget.clear()}
        title="Delete document?"
        description={`"${deleteTarget.target?.document_name}" will be permanently removed from the organization's knowledge base.`}
        confirmLabel="Delete"
        isPending={remove.isPending}
        onConfirm={handleConfirmDelete}
      />
    </div>
  );
}
