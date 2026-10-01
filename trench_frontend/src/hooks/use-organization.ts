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
  getMyOrganization,
  type InvitationRole,
  type ListMembersParams,
  type OrganizationRole,
} from "@/lib/organizations";

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

function invitationsQueryKey(organizationId: string) {
  return ["organization", organizationId, "invitations"] as const;
}

function invalidateInvitations(
  queryClient: ReturnType<typeof useQueryClient>,
  organizationId: string
) {
  queryClient.invalidateQueries({ queryKey: invitationsQueryKey(organizationId) });
}

export function useInvitations(organizationId: string) {
  return useQuery({
    queryKey: invitationsQueryKey(organizationId),
    queryFn: () => listInvitations(organizationId),
  });
}

export function useCreateInvitation(organizationId: string) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({ email, role }: { email: string; role: InvitationRole }) =>
      createInvitation(organizationId, email, role),
    onSuccess: () => invalidateInvitations(queryClient, organizationId),
  });
}

export function useResendInvitation(organizationId: string) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (invitationId: string) => resendInvitation(organizationId, invitationId),
    onSuccess: () => invalidateInvitations(queryClient, organizationId),
  });
}

export function useRevokeInvitation(organizationId: string) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (invitationId: string) => revokeInvitation(organizationId, invitationId),
    onSuccess: () => invalidateInvitations(queryClient, organizationId),
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
