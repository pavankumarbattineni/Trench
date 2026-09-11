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

export async function listMembers(
  organizationId: string
): Promise<OrganizationMember[]> {
  const { data } = await apiClient.get<OrganizationMember[]>(
    `/api/v1/organizations/${organizationId}/members`
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
  await apiClient.delete(`/api/v1/organizations/${organizationId}/members/${userId}`);
}

export async function grantKnowledgeAccess(
  organizationId: string,
  userId: string
): Promise<void> {
  await apiClient.post(
    `/api/v1/organizations/${organizationId}/knowledge-access/${userId}`
  );
}

export async function revokeKnowledgeAccess(
  organizationId: string,
  userId: string
): Promise<void> {
  await apiClient.delete(
    `/api/v1/organizations/${organizationId}/knowledge-access/${userId}`
  );
}
