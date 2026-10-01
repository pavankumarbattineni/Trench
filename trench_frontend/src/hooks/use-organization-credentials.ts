"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import {
  deleteOrganizationCredential,
  listOrganizationCredentials,
  saveOrganizationCredential,
} from "@/lib/organization-credentials";
import type { ProviderType } from "@/lib/credentials";

function organizationCredentialsQueryKey(organizationId: string) {
  return ["organization", organizationId, "credentials"] as const;
}

export function useOrganizationCredentials(organizationId: string) {
  return useQuery({
    queryKey: organizationCredentialsQueryKey(organizationId),
    queryFn: () => listOrganizationCredentials(organizationId),
  });
}

export function useSaveOrganizationCredential(organizationId: string) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({
      providerType,
      apiKey,
    }: {
      providerType: ProviderType;
      apiKey: string;
    }) => saveOrganizationCredential(organizationId, providerType, apiKey),
    onSuccess: () => {
      queryClient.invalidateQueries({
        queryKey: organizationCredentialsQueryKey(organizationId),
      });
    },
  });
}

export function useDeleteOrganizationCredential(organizationId: string) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (providerType: ProviderType) =>
      deleteOrganizationCredential(organizationId, providerType),
    onSuccess: () => {
      queryClient.invalidateQueries({
        queryKey: organizationCredentialsQueryKey(organizationId),
      });
    },
  });
}
