"use client";

import { useCallback, useState } from "react";
import { useDropzone, type FileRejection } from "react-dropzone";
import { Download, UploadCloud } from "lucide-react";
import { useMutation, useQueryClient } from "@tanstack/react-query";

import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { cn } from "cn";
import { getErrorMessage } from "@/lib/errors";
import { bulkCreateInvitations, type BulkInvitationResult } from "@/lib/organizations";

interface BulkInviteDialogProps {
  organizationId: string;
  open: boolean;
  onOpenChange: (open: boolean) => void;
}

const TEMPLATE_CSV = "email,role\njane@yourcompany.com,member\n";

function downloadTemplate() {
  const blob = new Blob([TEMPLATE_CSV], { type: "text/csv" });
  const url = URL.createObjectURL(blob);
  const link = document.createElement("a");
  link.href = url;
  link.download = "trench-employee-invite-template.csv";
  link.click();
  URL.revokeObjectURL(url);
}

export function BulkInviteDialog({
  organizationId,
  open,
  onOpenChange,
}: BulkInviteDialogProps) {
  const queryClient = useQueryClient();
  const [dropError, setDropError] = useState<string | null>(null);
  const [result, setResult] = useState<BulkInvitationResult | null>(null);

  const upload = useMutation({
    mutationFn: (file: File) => bulkCreateInvitations(organizationId, file),
    onSuccess: (data) => {
      setResult(data);
      queryClient.invalidateQueries({
        queryKey: ["organization", organizationId, "invitations"],
      });
    },
  });

  const onDrop = useCallback(
    (accepted: File[], rejections: FileRejection[]) => {
      setDropError(null);
      setResult(null);
      if (rejections.length > 0) {
        setDropError("Upload a .csv or .xlsx file.");
        return;
      }
      const file = accepted[0];
      if (file) upload.mutate(file);
    },
    [upload]
  );

  const { getRootProps, getInputProps, isDragActive } = useDropzone({
    onDrop,
    disabled: upload.isPending,
    multiple: false,
    accept: {
      "text/csv": [".csv"],
      "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet": [".xlsx"],
    },
  });

  const handleOpenChange = (next: boolean) => {
    if (!next) {
      setResult(null);
      setDropError(null);
    }
    onOpenChange(next);
  };

  return (
    <Dialog open={open} onOpenChange={handleOpenChange}>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>Bulk upload employees</DialogTitle>
          <DialogDescription>
            Upload a CSV or Excel file with <code>email</code> and{" "}
            <code>role</code> columns. Up to 50 people per upload -- if any
            row is invalid, the whole file is rejected and nobody is
            invited.
          </DialogDescription>
        </DialogHeader>

        <div className="space-y-4">
          <Button variant="outline" size="sm" onClick={downloadTemplate} type="button">
            <Download className="size-4" />
            Download CSV template
          </Button>

          <div
            {...getRootProps()}
            className={cn(
              "flex cursor-pointer flex-col items-center justify-center gap-2 rounded-2xl border-2 border-dashed border-border bg-muted/40 px-6 py-8 text-center transition-colors hover:border-primary/60 hover:bg-muted",
              isDragActive && "border-primary bg-muted",
              upload.isPending && "pointer-events-none opacity-50"
            )}
          >
            <input {...getInputProps()} />
            <UploadCloud className="size-6 text-muted-foreground" />
            <p className="text-sm font-medium">
              {upload.isPending
                ? "Uploading…"
                : isDragActive
                  ? "Drop the file here"
                  : "Drag and drop a file, or click to browse"}
            </p>
            <p className="text-xs text-muted-foreground">
              CSV or XLSX only, 50 rows max
            </p>
          </div>

          {dropError && <p className="text-sm text-destructive">{dropError}</p>}
          {upload.isError && (
            <p className="text-sm text-destructive">{getErrorMessage(upload.error)}</p>
          )}

          {result && (
            <div className="space-y-3 rounded-xl border border-border bg-card p-4">
              <p className="text-sm font-medium text-foreground">
                ✓ {result.succeeded.length} invitation
                {result.succeeded.length === 1 ? "" : "s"} sent
              </p>
              {result.failed.length > 0 && (
                <div className="space-y-1.5">
                  <p className="text-sm font-medium text-destructive">
                    ✗ {result.failed.length} row{result.failed.length === 1 ? "" : "s"}{" "}
                    failed
                  </p>
                  <ul className="space-y-1 text-xs text-muted-foreground">
                    {result.failed.map((row) => (
                      <li key={row.row}>
                        Row {row.row} ({row.email || "blank"}): {row.reason}
                      </li>
                    ))}
                  </ul>
                </div>
              )}
            </div>
          )}
        </div>
      </DialogContent>
    </Dialog>
  );
}
