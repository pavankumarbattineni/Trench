"use client";

import { useState, type KeyboardEvent } from "react";
import { ArrowUp, Square } from "lucide-react";
import { toast } from "sonner";

import { useAuth } from "@/components/auth-provider";
import { Button } from "@/components/ui/button";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { Textarea } from "@/components/ui/textarea";
import { useConfig } from "@/hooks/use-config";
import { useUpdateModel } from "@/hooks/use-update-model";
import type { KnowledgeType } from "@/lib/chat";
import { getErrorMessage } from "@/lib/errors";

interface ChatComposerProps {
  /** True while a message is in flight but there's no active stream yet
   * to offer stopping (e.g. creating the thread, or waiting on the
   * 202 Accepted) -- disables input without showing a Stop button. */
  isSending: boolean;
  /** True once a response is actively streaming -- shows Stop instead of
   * Send. */
  isStreaming: boolean;
  onSend: (query: string, knowledgeType: KnowledgeType) => void;
  onStop: () => void;
}

export function ChatComposer({
  isSending,
  isStreaming,
  onSend,
  onStop,
}: ChatComposerProps) {
  const isBusy = isSending || isStreaming;
  const { user } = useAuth();
  const { data: config } = useConfig();
  const updateModel = useUpdateModel();
  const [query, setQuery] = useState("");
  const [knowledgeType, setKnowledgeType] = useState<KnowledgeType>("personal");

  // Passed to Select's `items` prop (a value -> label map) so SelectValue
  // can resolve the trigger's displayed label immediately -- without it,
  // base-ui only learns an item's label once its SelectItem has actually
  // mounted inside an opened popup, so the trigger shows the raw value
  // (e.g. a model's UUID) until the user opens the dropdown once.
  const knowledgeTypeItems: Record<string, string> = {
    personal: "Personal knowledge",
    ...(user?.has_company_access ? { company: "Company knowledge" } : {}),
  };
  const modelItems: Record<string, string> = Object.fromEntries(
    (config?.llm_models ?? []).map((model) => [model.id, model.display_name])
  );

  const handleModelChange = (value: string | null) => {
    if (!value) return;
    updateModel.mutate(value, {
      onError: (err) => toast.error(getErrorMessage(err)),
    });
  };

  const handleSubmit = () => {
    const trimmed = query.trim();
    if (!trimmed || isBusy) return;
    onSend(trimmed, knowledgeType);
    setQuery("");
  };

  const handleKeyDown = (event: KeyboardEvent<HTMLTextAreaElement>) => {
    if (event.key === "Enter" && !event.shiftKey) {
      event.preventDefault();
      handleSubmit();
    }
  };

  return (
    <div className="mx-auto flex w-full max-w-3xl flex-col gap-2 p-4">
      <div className="flex items-end gap-2 rounded-2xl border border-border bg-card p-2">
        <Textarea
          value={query}
          onChange={(e) => setQuery(e.target.value)}
          onKeyDown={handleKeyDown}
          placeholder="Ask a question…"
          rows={1}
          disabled={isBusy}
          autoFocus
          className="max-h-40 min-h-9 flex-1 resize-none border-none bg-transparent shadow-none focus-visible:ring-0"
        />
        {isStreaming ? (
          <Button
            type="button"
            size="icon"
            variant="outline"
            aria-label="Stop generating"
            onClick={onStop}
          >
            <Square className="size-3.5 fill-current" />
          </Button>
        ) : (
          <Button
            type="button"
            size="icon"
            aria-label="Send message"
            disabled={!query.trim() || isBusy}
            onClick={handleSubmit}
          >
            <ArrowUp className="size-4" />
          </Button>
        )}
      </div>

      <div className="flex flex-wrap items-center gap-2">
        <Select
          items={knowledgeTypeItems}
          value={knowledgeType}
          onValueChange={(value) => setKnowledgeType(value as KnowledgeType)}
        >
          <SelectTrigger size="sm">
            <SelectValue />
          </SelectTrigger>
          <SelectContent>
            <SelectItem value="personal">Personal knowledge</SelectItem>
            {user?.has_company_access && (
              <SelectItem value="company">Company knowledge</SelectItem>
            )}
          </SelectContent>
        </Select>

        {config?.llm_models && config.llm_models.length > 0 && (
          <Select
            items={modelItems}
            value={user?.model_id ?? undefined}
            onValueChange={handleModelChange}
          >
            <SelectTrigger size="sm" className="min-w-[13rem]">
              <SelectValue placeholder="Model" />
            </SelectTrigger>
            <SelectContent className="max-h-60">
              {config.llm_models.map((model) => {
                const unusable = model.requires_api_key && !model.has_credential;
                return (
                  <SelectItem key={model.id} value={model.id} disabled={unusable}>
                    {model.display_name}
                    {unusable && (
                      <span className="text-muted-foreground"> (BYOK)</span>
                    )}
                  </SelectItem>
                );
              })}
            </SelectContent>
          </Select>
        )}
      </div>
    </div>
  );
}
