"use client";

import { keepPreviousData, useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { getTrace, getTraceStats, listTraces, syncTraces, type TraceListParams } from "@/lib/traces";

export function useTraces(params: TraceListParams) {
  return useQuery({
    queryKey: ["traces", "list", params],
    queryFn: () => listTraces(params),
    placeholderData: keepPreviousData,
  });
}

export function useTraceStats(days: number) {
  return useQuery({
    queryKey: ["traces", "stats", days],
    queryFn: () => getTraceStats(days),
    placeholderData: keepPreviousData,
  });
}

export function useTrace(traceId: string) {
  return useQuery({
    queryKey: ["traces", "detail", traceId],
    queryFn: () => getTrace(traceId),
    // Still arriving from LangSmith: look again shortly.
    refetchInterval: (query) => (query.state.data && !query.state.data.complete ? 5000 : false),
  });
}

export function useSyncTraces() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: syncTraces,
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["traces"] }),
  });
}
