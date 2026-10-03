"use client";

import { useQuery } from "@tanstack/react-query";

import { getConfig } from "@/lib/config";

export function useConfig() {
  return useQuery({
    queryKey: ["config"],
    queryFn: getConfig,
    // The model catalog changes only on deploys, not per-session.
    staleTime: 5 * 60 * 1000,
  });
}
