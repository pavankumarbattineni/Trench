"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import {
  deleteCredential,
  listCredentials,
  saveCredential,
  type ProviderType,
} from "@/lib/credentials";

export const credentialsQueryKey = ["credentials"] as const;

export function useCredentials() {
  return useQuery({ queryKey: credentialsQueryKey, queryFn: listCredentials });
}

export function useSaveCredential() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({ providerType, apiKey }: { providerType: ProviderType; apiKey: string }) =>
      saveCredential(providerType, apiKey),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: credentialsQueryKey });
    },
  });
}

export function useDeleteCredential() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: deleteCredential,
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: credentialsQueryKey });
    },
  });
}
