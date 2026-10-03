import { apiClient } from "@/lib/api";

export type TenantRole = "owner" | "admin" | "member";

export interface Tenant {
  id: string;
  name: string;
  domain: string;
  is_active: boolean;
  created_at: string;
}

export interface MyTenant extends Tenant {
  role: TenantRole;
}

export interface TenantMember {
  id: string;
  user_id: string;
  username: string;
  email: string;
  role: TenantRole;
  has_company_access: boolean;
  created_at: string;
}

export interface PaginatedMembers {
  items: TenantMember[];
  total: number;
  page: number;
  page_size: number;
  total_pages: number;
}

export async function getMyTenant(): Promise<MyTenant> {
  const { data } = await apiClient.get<MyTenant>("/api/v1/tenants/me");
  return data;
}

export interface ListMembersParams {
  page?: number;
  pageSize?: number;
  search?: string;
}

export async function listMembers(
  tenantId: string,
  { page = 1, pageSize = 10, search }: ListMembersParams = {}
): Promise<PaginatedMembers> {
  const { data } = await apiClient.get<PaginatedMembers>(
    `/api/v1/tenants/${tenantId}/members`,
    { params: { page, page_size: pageSize, search: search || undefined } }
  );
  return data;
}

export async function updateMemberRole(
  tenantId: string,
  userId: string,
  role: TenantRole
): Promise<TenantMember> {
  const { data } = await apiClient.patch<TenantMember>(
    `/api/v1/tenants/${tenantId}/members/${userId}`,
    { role }
  );
  return data;
}

export async function removeMember(
  tenantId: string,
  userId: string
): Promise<void> {
  await apiClient.delete(`/api/v1/tenants/${tenantId}/members`, {
    params: { user_id: userId },
  });
}

/** Removes every member of the tenant at once, except the permanent owner
 * and the caller themself. */
export async function removeAllMembers(
  tenantId: string
): Promise<{ removed_count: number }> {
  const { data } = await apiClient.delete<{ removed_count: number }>(
    `/api/v1/tenants/${tenantId}/members`,
    { params: { remove_all: true } }
  );
  return data;
}

/** Grants or revokes one member's company-knowledge access, depending on
 * `allowAccess`. Idempotent either way. */
export async function updateKnowledgeAccess(
  tenantId: string,
  userId: string,
  allowAccess: boolean
): Promise<TenantMember> {
  const { data } = await apiClient.post<TenantMember>(
    `/api/v1/tenants/${tenantId}/knowledge-access`,
    null,
    { params: { user_id: userId, allow_access: allowAccess } }
  );
  return data;
}

/** Revokes one member's company-knowledge access. */
export async function revokeKnowledgeAccess(
  tenantId: string,
  userId: string
): Promise<TenantMember> {
  const { data } = await apiClient.delete<TenantMember>(
    `/api/v1/tenants/${tenantId}/knowledge-access`,
    { params: { user_id: userId } }
  );
  return data;
}

/** Bulk-revokes company-knowledge access for every member of the tenant at
 * once. Tenant admins are unaffected -- their access comes from their
 * role, not a grant row. */
export async function removeAllKnowledgeAccess(
  tenantId: string
): Promise<{ revoked_count: number }> {
  const { data } = await apiClient.delete<{ revoked_count: number }>(
    `/api/v1/tenants/${tenantId}/knowledge-access`,
    { params: { remove_access: true } }
  );
  return data;
}

/** Bulk-grants company-knowledge access to every current member of the
 * tenant at once. */
export async function grantAllKnowledgeAccess(
  tenantId: string
): Promise<{ granted_count: number }> {
  const { data } = await apiClient.post<{ granted_count: number }>(
    `/api/v1/tenants/${tenantId}/knowledge-access`,
    null,
    { params: { access_all: true } }
  );
  return data;
}

export type InvitationStatus = "pending" | "accepted" | "revoked" | "expired";
export type InvitationRole = Exclude<TenantRole, "owner">;

export interface Invitation {
  id: string;
  email: string;
  role: InvitationRole;
  status: InvitationStatus;
  expires_at: string;
  created_at: string;
  accepted_at: string | null;
}

export async function createInvitation(
  tenantId: string,
  email: string,
  role: InvitationRole = "member"
): Promise<Invitation> {
  const formData = new FormData();
  formData.append("email", email);
  formData.append("role", role);
  const { data } = await apiClient.post<Invitation>(
    `/api/v1/tenants/${tenantId}/invitations`,
    formData
  );
  return data;
}

export async function listInvitations(tenantId: string): Promise<Invitation[]> {
  const { data } = await apiClient.get<Invitation[]>(
    `/api/v1/tenants/${tenantId}/invitations`
  );
  return data;
}

export async function resendInvitation(
  tenantId: string,
  invitationId: string
): Promise<Invitation> {
  const { data } = await apiClient.post<Invitation>(
    `/api/v1/tenants/${tenantId}/invitations/${invitationId}/resend`
  );
  return data;
}

export async function revokeInvitation(
  tenantId: string,
  invitationId: string
): Promise<void> {
  await apiClient.delete(
    `/api/v1/tenants/${tenantId}/invitations/${invitationId}`
  );
}

export interface BulkInvitationRowError {
  row: number;
  email: string;
  reason: string;
}

export interface BulkInvitationResult {
  succeeded: Invitation[];
  failed: BulkInvitationRowError[];
}

export async function bulkCreateInvitations(
  tenantId: string,
  file: File
): Promise<BulkInvitationResult> {
  const formData = new FormData();
  formData.append("file", file);
  const { data } = await apiClient.post<BulkInvitationResult>(
    `/api/v1/tenants/${tenantId}/invitations`,
    formData
  );
  return data;
}
