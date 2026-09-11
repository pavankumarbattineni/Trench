import { apiClient } from "@/lib/api";

export const PROVIDER_TYPES = [
  "openai_llm",
  "anthropic_llm",
  "gemini_llm",
  "pinecone",
] as const;
export type ProviderType = (typeof PROVIDER_TYPES)[number];

export const PROVIDER_LABELS: Record<ProviderType, string> = {
  openai_llm: "OpenAI",
  anthropic_llm: "Anthropic",
  gemini_llm: "Google Gemini",
  pinecone: "Pinecone",
};

export interface Credential {
  provider_type: ProviderType;
  masked_preview: string;
  validated_at: string;
}

export async function listCredentials(): Promise<Credential[]> {
  const { data } = await apiClient.get<{ credentials: Credential[] }>(
    "/api/v1/credentials"
  );
  return data.credentials;
}

export async function saveCredential(
  providerType: ProviderType,
  apiKey: string
): Promise<void> {
  await apiClient.post("/api/v1/credentials", {
    provider_type: providerType,
    api_key: apiKey,
  });
}

export async function deleteCredential(providerType: ProviderType): Promise<void> {
  await apiClient.delete(`/api/v1/credentials/${providerType}`);
}
