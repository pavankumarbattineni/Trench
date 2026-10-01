import { apiClient } from "@/lib/api";
import type { ProviderType } from "@/lib/credentials";

export interface OrganizationCredential {
  provider_type: ProviderType;
  masked_preview: string;
  validated_at: string;
}

/**
 * Organization-scoped credentials go through the same unified
 * /credentials API personal ones do (see lib/credentials.ts) -- passing
 * organization_id is what selects the organization's shared credential
 * instead of the caller's own. There is no separate
 * organization-credentials API on the backend.
 */
export async function listOrganizationCredentials(
  organizationId: string
): Promise<OrganizationCredential[]> {
  const { data } = await apiClient.get<{ credentials: OrganizationCredential[] }>(
    "/api/v1/credentials",
    { params: { organization_id: organizationId } }
  );
  return data.credentials;
}

export async function saveOrganizationCredential(
  organizationId: string,
  providerType: ProviderType,
  apiKey: string
): Promise<void> {
  await apiClient.post("/api/v1/credentials", {
    provider_type: providerType,
    api_key: apiKey,
    organization_id: organizationId,
  });
}

export async function deleteOrganizationCredential(
  organizationId: string,
  providerType: ProviderType
): Promise<void> {
  await apiClient.delete(`/api/v1/credentials/${providerType}`, {
    params: { organization_id: organizationId },
  });
}
