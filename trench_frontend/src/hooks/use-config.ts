"use client";

import { useQuery } from "@tanstack/react-query";

import { getConfig } from "@/lib/config";
import type { KnowledgeType } from "@/lib/chat";

export function useConfig(knowledgeType: KnowledgeType) {
  return useQuery({
    queryKey: ["config", knowledgeType],
    queryFn: () => getConfig(knowledgeType),
    // The model catalog (and each model's has_credential for this scope)
    // changes only on deploys/credential edits, not per-session.
    staleTime: 5 * 60 * 1000,
  });
}
