"use client";

import { useEffect, useRef, useState } from "react";
import { Check, Copy } from "lucide-react";

import { Button } from "@/components/ui/button";
import { Tooltip, TooltipContent, TooltipTrigger } from "@/components/ui/tooltip";

const FEEDBACK_MS = 2000;

/** Writes `text` to the clipboard. Falls back to a hidden textarea +
 * execCommand where the async Clipboard API is unavailable (e.g. the app
 * opened over a LAN IP, which isn't a secure context). */
async function writeClipboard(text: string): Promise<boolean> {
  try {
    if (navigator.clipboard && window.isSecureContext) {
      await navigator.clipboard.writeText(text);
      return true;
    }
  } catch {
    // fall through to the legacy path
  }
  const textarea = document.createElement("textarea");
  textarea.value = text;
  textarea.setAttribute("readonly", "");
  textarea.style.position = "fixed";
  textarea.style.opacity = "0";
  document.body.appendChild(textarea);
  textarea.select();
  try {
    return document.execCommand("copy");
  } catch {
    return false;
  } finally {
    textarea.remove();
  }
}

interface CopyButtonProps {
  text: string;
  /** What's being copied, for the tooltip and screen readers, e.g. "Copy message". */
  label: string;
}

export function CopyButton({ text, label }: CopyButtonProps) {
  const [status, setStatus] = useState<"idle" | "copied" | "failed">("idle");
  const [hovered, setHovered] = useState(false);
  const timerRef = useRef<ReturnType<typeof setTimeout> | null>(null);

  useEffect(() => () => {
    if (timerRef.current) clearTimeout(timerRef.current);
  }, []);

  const handleCopy = async () => {
    const ok = await writeClipboard(text);
    setStatus(ok ? "copied" : "failed");
    if (timerRef.current) clearTimeout(timerRef.current);
    timerRef.current = setTimeout(() => setStatus("idle"), FEEDBACK_MS);
  };

  const tooltip =
    status === "copied" ? "Copied!" : status === "failed" ? "Couldn't copy" : label;

  return (
    // `relative` keeps the absolutely-positioned sr-only live region below
    // inside this box -- without it, its containing block is the page root,
    // so it lands at its far-down spot in the message list and stretches
    // the whole document (an empty scrollable area below the app).
    <span className="relative inline-flex">
      {/* The tooltip stays open while "Copied!" shows, even if the pointer
          has already moved away -- the confirmation shouldn't be missable. */}
      <Tooltip open={hovered || status !== "idle"} onOpenChange={setHovered}>
        <TooltipTrigger
          render={
            <Button
              variant="ghost"
              size="icon-xs"
              aria-label={label}
              onClick={() => void handleCopy()}
              className="cursor-pointer text-muted-foreground hover:text-foreground"
            />
          }
        >
          {status === "copied" ? (
            <Check className="size-3.5 text-foreground" />
          ) : (
            <Copy className="size-3.5" />
          )}
        </TooltipTrigger>
        <TooltipContent side="bottom">{tooltip}</TooltipContent>
      </Tooltip>
      <span className="sr-only" aria-live="polite">
        {status === "copied" ? "Copied to clipboard" : status === "failed" ? "Couldn't copy" : ""}
      </span>
    </span>
  );
}
