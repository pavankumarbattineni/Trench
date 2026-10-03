"use client";

import { useState } from "react";
import { KeyRound, Trash2 } from "lucide-react";
import { toast } from "sonner";

import { ConfirmDialog, useConfirmTarget } from "@/components/confirm-dialog";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { Skeleton } from "@/components/ui/skeleton";
import { useCredentials, useDeleteCredential, useSaveCredential } from "@/hooks/use-credentials";
import { getErrorMessage } from "@/lib/errors";
import { PROVIDER_LABELS, PROVIDER_TYPES, type ProviderType } from "@/lib/credentials";

export function ApiKeysSection() {
  const { data: credentials, isLoading } = useCredentials();
  const saveCredential = useSaveCredential();
  const deleteCredential = useDeleteCredential();
  const deleteTarget = useConfirmTarget<ProviderType>();

  const [provider, setProvider] = useState<ProviderType>("openai_llm");
  const [apiKey, setApiKey] = useState("");
  const [error, setError] = useState<string | null>(null);

  const savedProviders = new Set(credentials?.map((c) => c.provider_type));
  const availableProviders = PROVIDER_TYPES.filter((p) => !savedProviders.has(p));

  const handleSave = () => {
    if (!apiKey.trim()) return;
    setError(null);
    saveCredential.mutate(
      { providerType: provider, apiKey: apiKey.trim() },
      {
        onSuccess: () => {
          setApiKey("");
          toast.success(`${PROVIDER_LABELS[provider]} key saved.`);
        },
        onError: (err) => setError(getErrorMessage(err)),
      }
    );
  };

  const handleConfirmDelete = () => {
    if (!deleteTarget.target) return;
    const target = deleteTarget.target;
    deleteCredential.mutate(target, {
      onSuccess: () => {
        toast.success(`${PROVIDER_LABELS[target]} key removed.`);
        deleteTarget.clear();
      },
      onError: (err) => {
        toast.error(getErrorMessage(err));
        deleteTarget.clear();
      },
    });
  };

  return (
    <div className="space-y-4">
      {isLoading && <Skeleton className="h-10 w-full rounded-lg" />}

      {!isLoading && credentials && credentials.length > 0 && (
        <div className="space-y-2">
          {credentials.map((credential) => (
            <div
              key={credential.provider_type}
              className="flex items-center gap-3 rounded-lg border border-border px-3 py-2"
            >
              <KeyRound className="size-4 shrink-0 text-muted-foreground" />
              <div className="min-w-0 flex-1">
                <p className="text-sm font-medium">
                  {PROVIDER_LABELS[credential.provider_type]}
                </p>
                <p className="font-mono text-xs text-muted-foreground">
                  {credential.masked_preview}
                </p>
              </div>
              <Button
                variant="ghost"
                size="icon-sm"
                aria-label={`Remove ${PROVIDER_LABELS[credential.provider_type]} key`}
                onClick={() => deleteTarget.request(credential.provider_type)}
              >
                <Trash2 className="size-4" />
              </Button>
            </div>
          ))}
        </div>
      )}

      {availableProviders.length > 0 ? (
        <div className="grid grid-cols-1 gap-3 sm:grid-cols-[1fr_1fr_auto] sm:gap-2">
          <div className="space-y-2">
            <Label htmlFor="provider-select">Provider</Label>
            <Select
              items={Object.fromEntries(
                availableProviders.map((p) => [p, PROVIDER_LABELS[p]])
              )}
              value={provider}
              onValueChange={(value) => value && setProvider(value as ProviderType)}
            >
              <SelectTrigger id="provider-select" className="w-full">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                {availableProviders.map((p) => (
                  <SelectItem key={p} value={p}>
                    {PROVIDER_LABELS[p]}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          </div>
          <div className="space-y-2">
            <Label htmlFor="api-key-input">API key</Label>
            <Input
              id="api-key-input"
              type="password"
              value={apiKey}
              onChange={(e) => setApiKey(e.target.value)}
              placeholder="sk-…"
              autoComplete="off"
            />
          </div>
          <div className="space-y-2">
            {/* Invisible label -- gives this column the same label-row
                height as the other two, so the Save button's own row
                lines up with the Select/Input row exactly, instead of
                relying on flex "items-end" to guess it. */}
            <Label aria-hidden className="invisible select-none">
              Save
            </Label>
            <Button
              className="w-full sm:w-auto"
              onClick={handleSave}
              disabled={!apiKey.trim() || saveCredential.isPending}
            >
              {saveCredential.isPending ? "Saving…" : "Save"}
            </Button>
          </div>
        </div>
      ) : (
        !isLoading && (
          <p className="text-sm text-muted-foreground">
            You&apos;ve added a key for every supported provider.
          </p>
        )
      )}
      {error && <p className="text-sm text-destructive">{error}</p>}

      <ConfirmDialog
        open={deleteTarget.open}
        onOpenChange={(open) => !open && deleteTarget.clear()}
        title="Remove API key?"
        description={
          deleteTarget.target
            ? `Trench will fall back to the platform default for ${PROVIDER_LABELS[deleteTarget.target]}.`
            : ""
        }
        confirmLabel="Remove"
        isPending={deleteCredential.isPending}
        onConfirm={handleConfirmDelete}
      />
    </div>
  );
}
