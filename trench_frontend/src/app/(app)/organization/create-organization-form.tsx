"use client";

import { useState } from "react";
import { Building2 } from "lucide-react";
import { toast } from "sonner";

import { useAuth } from "@/components/auth-provider";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { useCreateOrganization } from "@/hooks/use-organization";
import { getErrorMessage } from "@/lib/errors";

export function CreateOrganizationForm() {
  const { user } = useAuth();
  const [name, setName] = useState("");
  const [error, setError] = useState<string | null>(null);
  const createOrganization = useCreateOrganization();

  const domain = user?.email.split("@")[1] ?? "";

  const handleSubmit = (event: React.FormEvent) => {
    event.preventDefault();
    const trimmed = name.trim();
    if (!trimmed) return;
    setError(null);
    createOrganization.mutate(trimmed, {
      onSuccess: (organization) => {
        if (organization.auto_added_members > 0) {
          toast.success(
            `${organization.auto_added_members} existing ${domain} user${organization.auto_added_members === 1 ? "" : "s"} ${organization.auto_added_members === 1 ? "was" : "were"} added as member${organization.auto_added_members === 1 ? "" : "s"}.`
          );
        }
      },
      onError: (err) => setError(getErrorMessage(err)),
    });
  };

  return (
    <div className="mx-auto flex max-w-md flex-col items-center gap-6 py-16 text-center">
      <span className="flex size-12 items-center justify-center rounded-2xl bg-primary text-primary-foreground">
        <Building2 className="size-6" />
      </span>
      <div>
        <h1 className="text-xl font-semibold tracking-tight">Create your organization</h1>
        <p className="mt-1 text-sm text-muted-foreground">
          Your organization will be created for the{" "}
          <span className="font-medium text-foreground">{domain}</span> domain, based
          on your email. Only teammates with a {domain} email can join it.
        </p>
      </div>

      <form onSubmit={handleSubmit} className="w-full space-y-4 text-left">
        <div className="space-y-2">
          <Label htmlFor="org-name">Organization name</Label>
          <Input
            id="org-name"
            value={name}
            onChange={(e) => setName(e.target.value)}
            placeholder="Acme Inc."
            autoFocus
          />
        </div>
        {error && <p className="text-sm text-destructive">{error}</p>}
        <Button
          type="submit"
          className="w-full"
          disabled={!name.trim() || createOrganization.isPending}
        >
          {createOrganization.isPending ? "Creating…" : "Create organization"}
        </Button>
      </form>
    </div>
  );
}
