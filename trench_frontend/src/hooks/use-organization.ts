"use client";

import { keepPreviousData, useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { isAxiosError } from "axios";

import { useAuth } from "@/components/auth-provider";
import {
  addMember,
  createOrganization,
  getMyOrganization,
  grantAllKnowledgeAccess,
  listMembers,
  removeAllKnowledgeAccess,
  removeAllMembers,
  removeMember,
  revokeKnowledgeAccess,
  updateKnowledgeAccess,
  updateMemberRole,
  type ListMembersParams,
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

function membersQueryKey(organizationId: string | undefined, params: ListMembersParams) {
  return ["organization", organizationId, "members", params] as const;
}

export function useOrganizationMembers(
  organizationId: string | undefined,
  params: ListMembersParams = {}
) {
  return useQuery({
    queryKey: membersQueryKey(organizationId, params),
    queryFn: () => listMembers(organizationId!, params),
    enabled: Boolean(organizationId),
    // Keeps the previous page's rows on screen while a new page/search
    // loads, instead of flashing a loading state on every keystroke/click.
    placeholderData: keepPreviousData,
  });
}

function invalidateMembers(
  queryClient: ReturnType<typeof useQueryClient>,
  organizationId: string
) {
  queryClient.invalidateQueries({
    queryKey: ["organization", organizationId, "members"],
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
    onSuccess: () => invalidateMembers(queryClient, organizationId),
  });
}

export function useRemoveMember(organizationId: string) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (userId: string) => removeMember(organizationId, userId),
    onSuccess: () => invalidateMembers(queryClient, organizationId),
  });
}

export function useUpdateMemberRole(organizationId: string) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({ userId, role }: { userId: string; role: OrganizationRole }) =>
      updateMemberRole(organizationId, userId, role),
    onSuccess: () => invalidateMembers(queryClient, organizationId),
  });
}

export function useToggleKnowledgeAccess(organizationId: string) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({ userId, grant }: { userId: string; grant: boolean }) =>
      grant
        ? updateKnowledgeAccess(organizationId, userId, true)
        : revokeKnowledgeAccess(organizationId, userId),
    onSuccess: () => invalidateMembers(queryClient, organizationId),
  });
}

export function useRemoveAllKnowledgeAccess(organizationId: string) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: () => removeAllKnowledgeAccess(organizationId),
    onSuccess: () => invalidateMembers(queryClient, organizationId),
  });
}

export function useGrantAllKnowledgeAccess(organizationId: string) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: () => grantAllKnowledgeAccess(organizationId),
    onSuccess: () => invalidateMembers(queryClient, organizationId),
  });
}

export function useRemoveAllMembers(organizationId: string) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: () => removeAllMembers(organizationId),
    onSuccess: () => invalidateMembers(queryClient, organizationId),
  });
}
