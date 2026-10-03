"use client";

import { ApiKeyManager } from "@/components/settings/api-key-manager";
import {
  useDeleteTenantCredential,
  useTenantCredentials,
  useSaveTenantCredential,
} from "@/hooks/use-tenant-credentials";

interface TenantApiKeySectionProps {
  tenantId: string;
}

/** Owner-only: the tenant's shared BYOK credential, used for every
 * member's queries against the tenant's knowledge base. Distinct from
 * the personal API keys above -- see the explanatory copy below, shown
 * directly above the form so the scope distinction is visible at the
 * point of action, not buried in a tooltip. */
export function TenantApiKeySection({ tenantId }: TenantApiKeySectionProps) {
  return (
    <ApiKeyManager
      idPrefix="tenant-"
      credentialsQuery={useTenantCredentials(tenantId)}
      saveCredential={useSaveTenantCredential(tenantId)}
      deleteCredential={useDeleteTenantCredential(tenantId)}
      leadingContent={
        <p className="text-sm text-muted-foreground">
          This key is used for every member&apos;s queries against your
          tenant&apos;s shared knowledge base. Your personal API keys above
          are separate and only affect your own personal knowledge base.
        </p>
      }
      copy={{
        savedToast: (label) => `${label} key saved for the tenant.`,
        deletedToast: (label) => `${label} key removed from the tenant.`,
        allAddedMessage: "You've added a tenant key for every supported provider.",
        deleteAriaLabel: (label) => `Remove tenant ${label} key`,
        deleteDialogTitle: "Remove tenant API key?",
        deleteDialogDescription: (label) =>
          `Every member's tenant-knowledge queries will fall back to the platform default for ${label}.`,
      }}
    />
  );
}
