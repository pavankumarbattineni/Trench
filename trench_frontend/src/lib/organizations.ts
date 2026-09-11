import { apiClient } from "@/lib/api";

export type OrganizationRole = "admin" | "member";

export interface Organization {
  id: string;
  name: string;
  domain: string;
  owner_user_id: string;
  is_active: boolean;
  created_at: string;
}

export interface OrganizationCreated extends Organization {
  auto_added_members: number;
}

export interface MyOrganization extends Organization {
  my_role: OrganizationRole;
}

export interface OrganizationMember {
  id: string;
  user_id: string;
  username: string;
  email: string;
  role: OrganizationRole;
  has_company_access: boolean;
  is_owner: boolean;
  created_at: string;
}

export interface PaginatedMembers {
  items: OrganizationMember[];
  total: number;
  page: number;
  page_size: number;
  total_pages: number;
}

export async function createOrganization(name: string): Promise<OrganizationCreated> {
  const { data } = await apiClient.post<OrganizationCreated>(
    "/api/v1/organizations",
    { name }
  );
  return data;
}

export async function getMyOrganization(): Promise<MyOrganization> {
  const { data } = await apiClient.get<MyOrganization>("/api/v1/organizations/me");
  return data;
}

export interface ListMembersParams {
  page?: number;
  pageSize?: number;
  search?: string;
}

export async function listMembers(
  organizationId: string,
  { page = 1, pageSize = 10, search }: ListMembersParams = {}
): Promise<PaginatedMembers> {
  const { data } = await apiClient.get<PaginatedMembers>(
    `/api/v1/organizations/${organizationId}/members`,
    { params: { page, page_size: pageSize, search: search || undefined } }
  );
  return data;
}

export async function addMember(
  organizationId: string,
  username: string,
  role: OrganizationRole = "member"
): Promise<OrganizationMember> {
  const { data } = await apiClient.post<OrganizationMember>(
    `/api/v1/organizations/${organizationId}/members`,
    { username, role }
  );
  return data;
}

export async function updateMemberRole(
  organizationId: string,
  userId: string,
  role: OrganizationRole
): Promise<OrganizationMember> {
  const { data } = await apiClient.patch<OrganizationMember>(
    `/api/v1/organizations/${organizationId}/members/${userId}`,
    { role }
  );
  return data;
}

export async function removeMember(
  organizationId: string,
  userId: string
): Promise<void> {
  await apiClient.delete(`/api/v1/organizations/${organizationId}/members`, {
    params: { user_id: userId },
  });
}

/** Removes every member of the organization at once, except the
 * permanent owner and the caller themself. */
export async function removeAllMembers(
  organizationId: string
): Promise<{ removed_count: number }> {
  const { data } = await apiClient.delete<{ removed_count: number }>(
    `/api/v1/organizations/${organizationId}/members`,
    { params: { remove_all: true } }
  );
  return data;
}

/** Grants or revokes one member's company-knowledge access, depending on
 * `allowAccess`. Idempotent either way. */
export async function updateKnowledgeAccess(
  organizationId: string,
  userId: string,
  allowAccess: boolean
): Promise<OrganizationMember> {
  const { data } = await apiClient.post<OrganizationMember>(
    `/api/v1/organizations/${organizationId}/knowledge-access`,
    null,
    { params: { user_id: userId, allow_access: allowAccess } }
  );
  return data;
}

/** Revokes one member's company-knowledge access. */
export async function revokeKnowledgeAccess(
  organizationId: string,
  userId: string
): Promise<OrganizationMember> {
  const { data } = await apiClient.delete<OrganizationMember>(
    `/api/v1/organizations/${organizationId}/knowledge-access`,
    { params: { user_id: userId } }
  );
  return data;
}

/** Bulk-revokes company-knowledge access for every member of the
 * organization at once. Org admins are unaffected -- their access comes
 * from their role, not a grant row. */
export async function removeAllKnowledgeAccess(
  organizationId: string
): Promise<{ revoked_count: number }> {
  const { data } = await apiClient.delete<{ revoked_count: number }>(
    `/api/v1/organizations/${organizationId}/knowledge-access`,
    { params: { remove_access: true } }
  );
  return data;
}

/** Bulk-grants company-knowledge access to every current member of the
 * organization at once. */
export async function grantAllKnowledgeAccess(
  organizationId: string
): Promise<{ granted_count: number }> {
  const { data } = await apiClient.post<{ granted_count: number }>(
    `/api/v1/organizations/${organizationId}/knowledge-access`,
    null,
    { params: { access_all: true } }
  );
  return data;
}
