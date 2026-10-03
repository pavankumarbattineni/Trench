"use client";

import { ApiKeyManager } from "@/components/settings/api-key-manager";
import { useCredentials, useDeleteCredential, useSaveCredential } from "@/hooks/use-credentials";

export function ApiKeysSection() {
  return (
    <ApiKeyManager
      idPrefix=""
      credentialsQuery={useCredentials()}
      saveCredential={useSaveCredential()}
      deleteCredential={useDeleteCredential()}
      copy={{
        savedToast: (label) => `${label} key saved.`,
        deletedToast: (label) => `${label} key removed.`,
        allAddedMessage: "You've added a key for every supported provider.",
        deleteAriaLabel: (label) => `Remove ${label} key`,
        deleteDialogTitle: "Remove API key?",
        deleteDialogDescription: (label) =>
          `Trench will fall back to the platform default for ${label}.`,
      }}
    />
  );
}
