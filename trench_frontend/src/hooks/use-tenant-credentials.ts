"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { useAuth } from "@/components/auth-provider";
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
      // The company-scope /config list's has_credential reflects this
      // same tenant credential -- see lib/config.ts/use-config.ts.
      queryClient.invalidateQueries({ queryKey: ["config", "company"] });
    },
  });
}

export function useDeleteTenantCredential(tenantId: string) {
  const queryClient = useQueryClient();
  const { refreshUser } = useAuth();
  return useMutation({
    mutationFn: (providerType: ProviderType) =>
      deleteTenantCredential(tenantId, providerType),
    onSuccess: () => {
      queryClient.invalidateQueries({
        queryKey: tenantCredentialsQueryKey(tenantId),
      });
      queryClient.invalidateQueries({ queryKey: ["config", "company"] });
      // The backend may have reset the Owner's own selected model to the
      // platform default (see UserPreferenceService.
      // reset_models_requiring_credential) -- re-fetch to reflect it.
      void refreshUser();
    },
  });
}
