"use client";

import { useCallback } from "react";
import { useDropzone, type FileRejection } from "react-dropzone";
import { UploadCloud } from "lucide-react";
import { toast } from "sonner";

import { cn } from "cn";
import {
  ACCEPTED_DOCUMENT_TYPES,
  MAX_DOCUMENT_SIZE_BYTES,
} from "@/lib/documents";

interface UploadDropzoneProps {
  onFileSelected: (file: File) => void;
  disabled?: boolean;
  // Distinct from `disabled` (a transient in-flight-upload state, shown as
  // reduced opacity): this is the free-tier-limit-reached state, shown
  // blurred with a message overlay rather than just dimmed.
  blurred?: boolean;
  blurredMessage?: string;
}

export function UploadDropzone({
  onFileSelected,
  disabled,
  blurred,
  blurredMessage,
}: UploadDropzoneProps) {
  const onDrop = useCallback(
    (accepted: File[], rejections: FileRejection[]) => {
      if (rejections.length > 0) {
        const reason = rejections[0].errors[0]?.code;
        const message =
          reason === "file-too-large"
            ? "File is too large -- the limit is 20 MB."
            : "Unsupported file type. Trench accepts PDF, DOCX, TXT, and Markdown files.";
        toast.error(message);
        return;
      }
      if (accepted[0]) onFileSelected(accepted[0]);
    },
    [onFileSelected]
  );

  const { getRootProps, getInputProps, isDragActive } = useDropzone({
    onDrop,
    disabled: disabled || blurred,
    multiple: false,
    maxSize: MAX_DOCUMENT_SIZE_BYTES,
    accept: Object.fromEntries(ACCEPTED_DOCUMENT_TYPES.map((type) => [type, []])),
  });

  return (
    <div className="relative">
      <div
        {...getRootProps()}
        className={cn(
          "flex cursor-pointer flex-col items-center justify-center gap-2 rounded-2xl border-2 border-dashed border-border bg-muted/40 px-6 py-10 text-center transition-colors hover:border-primary/60 hover:bg-muted",
          isDragActive && "border-primary bg-muted",
          disabled && "pointer-events-none opacity-50",
          blurred && "pointer-events-none blur-sm"
        )}
      >
        <input {...getInputProps()} />
        <UploadCloud className="size-8 text-muted-foreground" />
        <p className="text-sm font-medium">
          {isDragActive ? "Drop the file here" : "Drag and drop a file, or click to browse"}
        </p>
        <p className="text-xs text-muted-foreground">PDF, DOCX, TXT, or Markdown -- up to 20 MB</p>
      </div>
      {blurred && (
        <div className="absolute inset-0 flex items-center justify-center">
          <p className="rounded-lg bg-background/90 px-4 py-2 text-sm font-medium shadow-sm">
            {blurredMessage ?? "Document limit reached"}
          </p>
        </div>
      )}
    </div>
  );
}
