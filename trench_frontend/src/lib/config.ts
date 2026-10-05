import { apiClient } from "@/lib/api";
import type { KnowledgeType } from "@/lib/chat";

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

// A model's `has_credential` depends on which knowledge base it would be
// used with -- a personal BYOK key never backs a company query and vice
// versa (see app/service/llm_client_service.py:resolve_for_knowledge on
// the backend), so the catalog must be re-fetched per scope rather than
// cached once globally.
export async function getConfig(knowledgeType: KnowledgeType): Promise<AppConfig> {
  const { data } = await apiClient.get<AppConfig>("/api/v1/config", {
    params: { knowledge_type: knowledgeType },
  });
  return data;
}
