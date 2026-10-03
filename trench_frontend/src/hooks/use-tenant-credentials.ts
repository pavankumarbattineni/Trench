"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import {
  deleteTenantCredential,
  listTenantCredentials,
  saveTenantCredential,
} from "@/lib/tenant-credentials";
import type { ProviderType } from "@/lib/credentials";

function tenantCredentialsQueryKey(tenantId: string) {
  return ["tenant", tenantId, "credentials"] as const;
}

export function useTenantCredentials(tenantId: string) {
  return useQuery({
    queryKey: tenantCredentialsQueryKey(tenantId),
    queryFn: () => listTenantCredentials(tenantId),
  });
}

export function useSaveTenantCredential(tenantId: string) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({
      providerType,
      apiKey,
    }: {
      providerType: ProviderType;
      apiKey: string;
    }) => saveTenantCredential(tenantId, providerType, apiKey),
    onSuccess: () => {
      queryClient.invalidateQueries({
        queryKey: tenantCredentialsQueryKey(tenantId),
      });
    },
  });
}

export function useDeleteTenantCredential(tenantId: string) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (providerType: ProviderType) =>
      deleteTenantCredential(tenantId, providerType),
    onSuccess: () => {
      queryClient.invalidateQueries({
        queryKey: tenantCredentialsQueryKey(tenantId),
      });
    },
  });
}
