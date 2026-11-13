import { cn } from "cn";

import { statusLabel, type TraceStatus } from "@/lib/traces";

export function StatusDot({ status, className }: { status: TraceStatus; className?: string }) {
  const label = statusLabel(status);
  return (
    <span
      role="img"
      aria-label={label}
      title={label}
      className={cn(
        "inline-block size-2 shrink-0 rounded-full",
        status === "error" && "bg-destructive",
        status === "pending" && "animate-pulse bg-[var(--viz-4)]",
        status === "success" && "bg-[var(--viz-3)]",
        className
      )}
    />
  );
}
