"use client";

import { keepPreviousData, useQuery } from "@tanstack/react-query";

import { getUsageQueries, getUsageSummary } from "@/lib/usage";

export function useUsageSummary(days: number) {
  return useQuery({
    queryKey: ["usage", "summary", days],
    queryFn: () => getUsageSummary(days),
    placeholderData: keepPreviousData,
  });
}

export function useUsageQueries(days: number, page: number) {
  return useQuery({
    queryKey: ["usage", "queries", days, page],
    queryFn: () => getUsageQueries(days, page),
    placeholderData: keepPreviousData,
  });
}
