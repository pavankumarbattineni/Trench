"use client";

import { useEffect, useRef } from "react";
import { FileText, X } from "lucide-react";

import { Button } from "@/components/ui/button";
import { Sheet, SheetContent, SheetHeader, SheetTitle } from "@/components/ui/sheet";
import type { RetrievedChunk } from "@/lib/threads";
import { cn } from "cn";

export interface CitationSelection {
  chunks: RetrievedChunk[];
  /** 1-indexed, matching the "[n]" markers in the message that opened
   * this selection -- null when opened via the "N sources" summary
   * button rather than a specific marker. */
  activeIndex: number | null;
}

function PanelBody({ chunks, activeIndex }: CitationSelection) {
  const activeRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    activeRef.current?.scrollIntoView({ behavior: "smooth", block: "nearest" });
  }, [activeIndex]);

  return (
    <div className="flex flex-col gap-3 overflow-y-auto p-4">
      {chunks.map((chunk, i) => {
        const index = i + 1;
        const isActive = index === activeIndex;
        return (
          <div
            key={chunk.chunk_id}
            ref={isActive ? activeRef : undefined}
            className={cn(
              "rounded-xl border p-3 text-sm transition-colors",
              isActive ? "border-primary bg-primary/5" : "border-border bg-card"
            )}
          >
            <div className="mb-1.5 flex items-center justify-between gap-2">
              <div className="flex min-w-0 items-center gap-1.5 text-xs font-medium text-muted-foreground">
                <FileText className="size-3.5 shrink-0" />
                <span className="truncate">{chunk.document_name}</span>
              </div>
              <span className="shrink-0 rounded bg-muted px-1.5 py-0.5 text-[0.65rem] font-medium text-muted-foreground">
                [{index}]
              </span>
            </div>
            <p className="whitespace-pre-wrap text-foreground/90">{chunk.content}</p>
          </div>
        );
      })}
    </div>
  );
}

interface CitationPanelProps {
  selection: CitationSelection | null;
  onClose: () => void;
}

/** Renders sources in a right-side panel (desktop) or a slide-over sheet
 * (mobile), keeping the conversation itself visible and untouched --
 * clicking a citation never navigates away from the chat. */
export function CitationPanel({ selection, onClose }: CitationPanelProps) {
  const open = selection !== null;

  return (
    <>
      <aside
        className={cn(
          "hidden shrink-0 flex-col border-l border-border bg-background transition-[width] duration-200 md:flex",
          open ? "w-96" : "w-0 overflow-hidden border-l-0"
        )}
      >
        {selection && (
          <>
            <div className="flex items-center justify-between border-b border-border p-3">
              <h2 className="text-sm font-medium">
                Sources ({selection.chunks.length})
              </h2>
              <Button
                variant="ghost"
                size="icon-sm"
                aria-label="Close sources panel"
                onClick={onClose}
              >
                <X className="size-4" />
              </Button>
            </div>
            <PanelBody {...selection} />
          </>
        )}
      </aside>

      <div className="md:hidden">
        <Sheet open={open} onOpenChange={(next) => !next && onClose()}>
          <SheetContent side="right" className="w-full p-0 sm:max-w-md">
            <SheetHeader className="border-b border-border">
              <SheetTitle>
                Sources {selection ? `(${selection.chunks.length})` : ""}
              </SheetTitle>
            </SheetHeader>
            {selection && <PanelBody {...selection} />}
          </SheetContent>
        </Sheet>
      </div>
    </>
  );
}
