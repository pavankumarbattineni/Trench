"use client";

import { keepPreviousData, useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { isAxiosError } from "axios";

import {
  createInvitation,
  grantAllKnowledgeAccess,
  listInvitations,
  listMembers,
  removeAllKnowledgeAccess,
  removeAllMembers,
  removeMember,
  resendInvitation,
  revokeInvitation,
  revokeKnowledgeAccess,
  updateKnowledgeAccess,
  updateMemberRole,
  getMyTenant,
  type InvitationRole,
  type ListMembersParams,
  type TenantRole,
} from "@/lib/tenants";

export const myTenantQueryKey = ["tenant", "me"] as const;

export function useMyTenant() {
  return useQuery({
    queryKey: myTenantQueryKey,
    queryFn: getMyTenant,
    retry: (failureCount, error) => {
      // 404 means "not in a tenant" -- a normal, expected state, not a
      // transient failure worth retrying.
      if (isAxiosError(error) && error.response?.status === 404) return false;
      return failureCount < 2;
    },
  });
}

function membersQueryKey(tenantId: string | undefined, params: ListMembersParams) {
  return ["tenant", tenantId, "members", params] as const;
}

export function useTenantMembers(
  tenantId: string | undefined,
  params: ListMembersParams = {}
) {
  return useQuery({
    queryKey: membersQueryKey(tenantId, params),
    queryFn: () => listMembers(tenantId!, params),
    enabled: Boolean(tenantId),
    // Keeps the previous page's rows on screen while a new page/search
    // loads, instead of flashing a loading state on every keystroke/click.
    placeholderData: keepPreviousData,
  });
}

function invalidateMembers(
  queryClient: ReturnType<typeof useQueryClient>,
  tenantId: string
) {
  queryClient.invalidateQueries({
    queryKey: ["tenant", tenantId, "members"],
  });
}

function invitationsQueryKey(tenantId: string) {
  return ["tenant", tenantId, "invitations"] as const;
}

function invalidateInvitations(
  queryClient: ReturnType<typeof useQueryClient>,
  tenantId: string
) {
  queryClient.invalidateQueries({ queryKey: invitationsQueryKey(tenantId) });
}

export function useInvitations(tenantId: string) {
  return useQuery({
    queryKey: invitationsQueryKey(tenantId),
    queryFn: () => listInvitations(tenantId),
  });
}

export function useCreateInvitation(tenantId: string) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({ email, role }: { email: string; role: InvitationRole }) =>
      createInvitation(tenantId, email, role),
    onSuccess: () => invalidateInvitations(queryClient, tenantId),
  });
}

export function useResendInvitation(tenantId: string) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (invitationId: string) => resendInvitation(tenantId, invitationId),
    onSuccess: () => invalidateInvitations(queryClient, tenantId),
  });
}

export function useRevokeInvitation(tenantId: string) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (invitationId: string) => revokeInvitation(tenantId, invitationId),
    onSuccess: () => invalidateInvitations(queryClient, tenantId),
  });
}

export function useRemoveMember(tenantId: string) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (userId: string) => removeMember(tenantId, userId),
    onSuccess: () => invalidateMembers(queryClient, tenantId),
  });
}

export function useUpdateMemberRole(tenantId: string) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({ userId, role }: { userId: string; role: TenantRole }) =>
      updateMemberRole(tenantId, userId, role),
    onSuccess: () => invalidateMembers(queryClient, tenantId),
  });
}

export function useToggleKnowledgeAccess(tenantId: string) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({ userId, grant }: { userId: string; grant: boolean }) =>
      grant
        ? updateKnowledgeAccess(tenantId, userId, true)
        : revokeKnowledgeAccess(tenantId, userId),
    onSuccess: () => invalidateMembers(queryClient, tenantId),
  });
}

export function useRemoveAllKnowledgeAccess(tenantId: string) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: () => removeAllKnowledgeAccess(tenantId),
    onSuccess: () => invalidateMembers(queryClient, tenantId),
  });
}

export function useGrantAllKnowledgeAccess(tenantId: string) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: () => grantAllKnowledgeAccess(tenantId),
    onSuccess: () => invalidateMembers(queryClient, tenantId),
  });
}

export function useRemoveAllMembers(tenantId: string) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: () => removeAllMembers(tenantId),
    onSuccess: () => invalidateMembers(queryClient, tenantId),
  });
}
