import { CheckCircle2, CircleAlert, Clock, Loader2 } from "lucide-react";

import { Badge } from "@/components/ui/badge";
import type { DocumentStatus } from "@/lib/documents";

const STATUS_CONFIG: Record<
  DocumentStatus,
  { label: string; variant: "secondary" | "outline" | "destructive"; icon: typeof Clock }
> = {
  pending: { label: "Pending", variant: "outline", icon: Clock },
  processing: { label: "Processing", variant: "outline", icon: Loader2 },
  completed: { label: "Ready", variant: "secondary", icon: CheckCircle2 },
  failed: { label: "Failed", variant: "destructive", icon: CircleAlert },
};

export function DocumentStatusBadge({ status }: { status: DocumentStatus }) {
  const config = STATUS_CONFIG[status];
  const Icon = config.icon;
  return (
    <Badge variant={config.variant} data-icon="inline-start">
      <Icon className={status === "processing" ? "animate-spin" : undefined} />
      {config.label}
    </Badge>
  );
}
