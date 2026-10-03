"use client";

import { useState } from "react";
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

  return (
    <div
      className={cn(
        "group/thread flex w-full items-center rounded-lg",
        (active || menuOpen) && "bg-muted"
      )}
    >
      <Link
        href={href}
        onClick={onNavigate}
        className="min-w-0 flex-1 truncate px-2 py-1.5 text-sm text-foreground"
      >
        {thread.title ?? (
          <span className="text-muted-foreground italic">New conversation</span>
        )}
      </Link>

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
