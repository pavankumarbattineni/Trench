import { apiClient } from "@/lib/api";

export interface LlmModel {
  id: string;
  provider_name: string;
  model_name: string;
  display_name: string;
  is_platform_default: boolean;
}

export interface AppConfig {
  llm_models: LlmModel[];
}

export async function getConfig(): Promise<AppConfig> {
  const { data } = await apiClient.get<AppConfig>("/api/v1/config");
  return data;
}
