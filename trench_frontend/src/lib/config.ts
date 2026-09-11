import { apiClient } from "@/lib/api";

export interface LlmModel {
  id: string;
  provider_name: string;
  model_name: string;
  display_name: string;
  is_platform_default: boolean;
  /** False only for the platform-owned default (Groq) -- every other
   * provider needs the user's own BYOK credential. */
  requires_api_key: boolean;
  /** Whether the *current* user already has the credential this model
   * needs (always true when `requires_api_key` is false). A UX
   * convenience for disabling unusable models in the picker -- the
   * backend independently re-validates on selection, so this is never
   * the actual security boundary. */
  has_credential: boolean;
}

export interface AppConfig {
  llm_models: LlmModel[];
}

export async function getConfig(): Promise<AppConfig> {
  const { data } = await apiClient.get<AppConfig>("/api/v1/config");
  return data;
}
