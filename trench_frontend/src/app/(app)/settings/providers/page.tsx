"use client";

import { Sparkles } from "lucide-react";

import { ApiKeysSection } from "@/components/settings/api-keys-section";
import { SettingsSection } from "@/components/settings/settings-section";
import { Badge } from "@/components/ui/badge";
import { Skeleton } from "@/components/ui/skeleton";
import { useConfig } from "@/hooks/use-config";
import { getErrorMessage } from "@/lib/errors";

export default function ProvidersPage() {
  const { data: config, isLoading, isError, error } = useConfig();

  return (
    <div className="flex flex-col gap-6">
      <div className="rounded-2xl border border-border bg-card">
        <SettingsSection
          title="Providers"
          description="Trench uses a free platform-default model for chat. Bring your own API key below to use OpenAI, Anthropic, or Gemini instead, or to connect your own Pinecone index."
        >
          <ApiKeysSection />
        </SettingsSection>
      </div>

      <div className="rounded-2xl border border-border bg-card">
        <SettingsSection
          title="Available models"
          description="The chat models currently available on Trench, grouped by provider."
        >
          {isLoading && (
            <div className="space-y-2">
              <Skeleton className="h-10 w-full rounded-lg" />
              <Skeleton className="h-10 w-full rounded-lg" />
            </div>
          )}
          {isError && <p className="text-sm text-destructive">{getErrorMessage(error)}</p>}
          {config && (
            <div className="space-y-2">
              {config.llm_models.map((model) => (
                <div
                  key={model.id}
                  className="flex items-center gap-3 rounded-lg border border-border px-3 py-2"
                >
                  <Sparkles className="size-4 shrink-0 text-muted-foreground" />
                  <div className="min-w-0 flex-1">
                    <p className="truncate text-sm font-medium">{model.display_name}</p>
                    <p className="truncate text-xs text-muted-foreground capitalize">
                      {model.provider_name}
                    </p>
                  </div>
                  {model.is_platform_default && (
                    <Badge variant="secondary">Default</Badge>
                  )}
                </div>
              ))}
            </div>
          )}
        </SettingsSection>
      </div>
    </div>
  );
}
