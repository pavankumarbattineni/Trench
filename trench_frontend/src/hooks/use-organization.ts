"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { isAxiosError } from "axios";

import { useAuth } from "@/components/auth-provider";
import {
  addMember,
  createOrganization,
  getMyOrganization,
  grantKnowledgeAccess,
  listMembers,
  removeMember,
  revokeKnowledgeAccess,
  updateMemberRole,
  type OrganizationRole,
} from "@/lib/organizations";
import { getCurrentUser } from "@/lib/api";

export const myOrganizationQueryKey = ["organization", "me"] as const;

export function useMyOrganization() {
  return useQuery({
    queryKey: myOrganizationQueryKey,
    queryFn: getMyOrganization,
    retry: (failureCount, error) => {
      // 404 means "not in an organization" -- a normal, expected state,
      // not a transient failure worth retrying.
      if (isAxiosError(error) && error.response?.status === 404) return false;
      return failureCount < 2;
    },
  });
}

export function useOrganizationMembers(organizationId: string | undefined) {
  return useQuery({
    queryKey: ["organization", organizationId, "members"],
    queryFn: () => listMembers(organizationId!),
    enabled: Boolean(organizationId),
  });
}

export function useCreateOrganization() {
  const queryClient = useQueryClient();
  const { setUser } = useAuth();
  return useMutation({
    mutationFn: createOrganization,
    onSuccess: async () => {
      queryClient.invalidateQueries({ queryKey: myOrganizationQueryKey });
      // The user's own profile (organization, has_company_access) is now
      // stale -- refresh it so the rest of the app (e.g. the chat
      // composer's knowledge-type selector) reflects membership right
      // away, not just after a full page reload.
      setUser(await getCurrentUser());
    },
  });
}

export function useAddMember(organizationId: string) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({ username, role }: { username: string; role: OrganizationRole }) =>
      addMember(organizationId, username, role),
    onSuccess: () => {
      queryClient.invalidateQueries({
        queryKey: ["organization", organizationId, "members"],
      });
    },
  });
}

export function useRemoveMember(organizationId: string) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (userId: string) => removeMember(organizationId, userId),
    onSuccess: () => {
      queryClient.invalidateQueries({
        queryKey: ["organization", organizationId, "members"],
      });
    },
  });
}

export function useUpdateMemberRole(organizationId: string) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({ userId, role }: { userId: string; role: OrganizationRole }) =>
      updateMemberRole(organizationId, userId, role),
    onSuccess: () => {
      queryClient.invalidateQueries({
        queryKey: ["organization", organizationId, "members"],
      });
    },
  });
}

export function useToggleKnowledgeAccess(organizationId: string) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({ userId, grant }: { userId: string; grant: boolean }) =>
      grant
        ? grantKnowledgeAccess(organizationId, userId)
        : revokeKnowledgeAccess(organizationId, userId),
    onSuccess: () => {
      queryClient.invalidateQueries({
        queryKey: ["organization", organizationId, "members"],
      });
    },
  });
}
