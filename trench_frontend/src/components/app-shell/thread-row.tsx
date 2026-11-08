"use client";

import { useRef, useState } from "react";
import Link from "next/link";
import { MoreHorizontal, Pencil, Trash2 } from "lucide-react";

import { Button } from "@/components/ui/button";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { PromptDialog } from "@/components/prompt-dialog";
import { Tooltip, TooltipContent, TooltipTrigger } from "@/components/ui/tooltip";
import { useRenameThread } from "@/hooks/use-threads";
import { getErrorMessage } from "@/lib/errors";
import type { ThreadSummary } from "@/lib/threads";
import { cn } from "cn";

interface ThreadRowProps {
  thread: ThreadSummary;
  active: boolean;
  onNavigate?: () => void;
  onRequestDelete: (thread: ThreadSummary) => void;
}

export function ThreadRow({
  thread,
  active,
  onNavigate,
  onRequestDelete,
}: ThreadRowProps) {
  const href = `/chat/${thread.id}`;
  const [renaming, setRenaming] = useState(false);
  const [menuOpen, setMenuOpen] = useState(false);
  const rename = useRenameThread(thread.id);

  // The sidebar keeps long titles truncated; the full title shows in a
  // themed tooltip to the right instead -- but only when the title is
  // actually cut off (nothing to reveal otherwise).
  const linkRef = useRef<HTMLAnchorElement>(null);
  const [tooltipOpen, setTooltipOpen] = useState(false);
  const handleTooltipOpenChange = (open: boolean) => {
    const el = linkRef.current;
    const truncated = !!el && el.scrollWidth > el.clientWidth;
    setTooltipOpen(open && truncated && !!thread.title);
  };

  return (
    <div
      className={cn(
        "group/thread flex w-full items-center rounded-lg",
        (active || menuOpen) && "bg-muted"
      )}
    >
      <Tooltip open={tooltipOpen} onOpenChange={handleTooltipOpenChange}>
        <TooltipTrigger
          render={
            <Link
              ref={linkRef}
              href={href}
              onClick={onNavigate}
              className="min-w-0 flex-1 truncate px-2 py-1.5 text-sm text-foreground"
            />
          }
        >
          {thread.title ?? (
            <span className="text-muted-foreground italic">New conversation</span>
          )}
        </TooltipTrigger>
        <TooltipContent side="right" sideOffset={8} className="break-words">
          {thread.title}
        </TooltipContent>
      </Tooltip>

      <DropdownMenu open={menuOpen} onOpenChange={setMenuOpen}>
        <DropdownMenuTrigger
          render={
            <Button
              variant="ghost"
              size="icon-xs"
              className={cn(
                "mr-1 shrink-0 opacity-0 group-hover/thread:opacity-100",
                menuOpen && "opacity-100"
              )}
              aria-label="Conversation options"
            >
              <MoreHorizontal className="size-3.5" />
            </Button>
          }
        />
        <DropdownMenuContent align="start">
          <DropdownMenuItem onClick={() => setRenaming(true)}>
            <Pencil className="size-4" />
            Rename
          </DropdownMenuItem>
          <DropdownMenuItem
            variant="destructive"
            onClick={() => onRequestDelete(thread)}
          >
            <Trash2 className="size-4" />
            Delete
          </DropdownMenuItem>
        </DropdownMenuContent>
      </DropdownMenu>

      <PromptDialog
        open={renaming}
        onOpenChange={setRenaming}
        title="Rename conversation"
        initialValue={thread.title ?? ""}
        placeholder="Conversation name"
        confirmLabel="Rename"
        isPending={rename.isPending}
        error={rename.error ? getErrorMessage(rename.error) : null}
        onSubmit={(title) =>
          rename.mutate(title, { onSuccess: () => setRenaming(false) })
        }
      />
    </div>
  );
}
