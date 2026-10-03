import { apiClient } from "@/lib/api";
import type { ProviderType } from "@/lib/credentials";

export interface TenantCredential {
  provider_type: ProviderType;
  masked_preview: string;
  validated_at: string;
}

/**
 * Tenant-scoped credentials go through the same unified /credentials API
 * personal ones do (see lib/credentials.ts) -- passing tenant_id is what
 * selects the tenant's shared credential instead of the caller's own.
 * There is no separate tenant-credentials API on the backend.
 */
export async function listTenantCredentials(
  tenantId: string
): Promise<TenantCredential[]> {
  const { data } = await apiClient.get<{ credentials: TenantCredential[] }>(
    "/api/v1/credentials",
    { params: { tenant_id: tenantId } }
  );
  return data.credentials;
}

export async function saveTenantCredential(
  tenantId: string,
  providerType: ProviderType,
  apiKey: string
): Promise<void> {
  await apiClient.post("/api/v1/credentials", {
    provider_type: providerType,
    api_key: apiKey,
    tenant_id: tenantId,
  });
}

export async function deleteTenantCredential(
  tenantId: string,
  providerType: ProviderType
): Promise<void> {
  await apiClient.delete(`/api/v1/credentials/${providerType}`, {
    params: { tenant_id: tenantId },
  });
}
