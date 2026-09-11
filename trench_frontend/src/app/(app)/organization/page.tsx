"use client";

import { useState } from "react";
import { isAxiosError } from "axios";
import { Building2, FileText, Users } from "lucide-react";

import { useAuth } from "@/components/auth-provider";
import { Badge } from "@/components/ui/badge";
import { Skeleton } from "@/components/ui/skeleton";
import { cn } from "cn";
import { useMyOrganization } from "@/hooks/use-organization";
import { getErrorMessage } from "@/lib/errors";
import { CreateOrganizationForm } from "./create-organization-form";
import { MembersPanel } from "./members-panel";
import { OrganizationDocumentsPanel } from "./organization-documents-panel";

type OrgTab = "members" | "documents";

export default function OrganizationPage() {
  const { user } = useAuth();
  const orgQuery = useMyOrganization();
  const notInOrg = isAxiosError(orgQuery.error) && orgQuery.error.response?.status === 404;

  if (orgQuery.isLoading) {
    return (
      <main className="mx-auto flex h-full max-w-3xl flex-col gap-4 overflow-y-auto px-4 py-10">
        <Skeleton className="h-8 w-48" />
        <Skeleton className="h-24 w-full rounded-2xl" />
        <Skeleton className="h-16 w-full rounded-xl" />
        <Skeleton className="h-16 w-full rounded-xl" />
      </main>
    );
  }

  if (notInOrg) {
    return (
      <main className="mx-auto h-full max-w-3xl overflow-y-auto px-4">
        <CreateOrganizationForm />
      </main>
    );
  }

  if (orgQuery.isError || !orgQuery.data) {
    return (
      <main className="mx-auto flex h-full max-w-3xl items-center justify-center px-4 text-center">
        <p className="text-sm text-destructive">{getErrorMessage(orgQuery.error)}</p>
      </main>
    );
  }

  const organization = orgQuery.data;
  const isAdmin = organization.my_role === "admin";

  return (
    <OrganizationDetail
      organizationId={organization.id}
      name={organization.name}
      domain={organization.domain}
      createdAt={organization.created_at}
      isAdmin={isAdmin}
      hasCompanyAccess={Boolean(user?.has_company_access)}
      currentUserId={user?.id}
    />
  );
}

function OrganizationDetail({
  organizationId,
  name,
  domain,
  createdAt,
  isAdmin,
  hasCompanyAccess,
  currentUserId,
}: {
  organizationId: string;
  name: string;
  domain: string;
  createdAt: string;
  isAdmin: boolean;
  hasCompanyAccess: boolean;
  currentUserId: string | undefined;
}) {
  const [tab, setTab] = useState<OrgTab>("members");

  return (
    <main className="mx-auto flex h-full max-w-3xl flex-col gap-6 overflow-y-auto px-4 py-10">
      <div className="flex items-start gap-4 rounded-2xl border border-border bg-card p-5">
        <span className="flex size-12 shrink-0 items-center justify-center rounded-xl bg-primary text-primary-foreground">
          <Building2 className="size-6" />
        </span>
        <div className="min-w-0 flex-1">
          <h1 className="truncate text-xl font-semibold tracking-tight">{name}</h1>
          <p className="text-sm text-muted-foreground">
            {domain} · Created {new Date(createdAt).toLocaleDateString()}
          </p>
        </div>
        {isAdmin && (
          <Badge variant="default" data-icon="inline-start">
            Admin
          </Badge>
        )}
      </div>

      <div className="flex gap-1 border-b border-border">
        <button
          type="button"
          onClick={() => setTab("members")}
          className={cn(
            "flex items-center gap-2 border-b-2 px-3 py-2 text-sm font-medium transition-colors",
            tab === "members"
              ? "border-primary text-foreground"
              : "border-transparent text-muted-foreground hover:text-foreground"
          )}
        >
          <Users className="size-4" />
          Members
        </button>
        <button
          type="button"
          onClick={() => setTab("documents")}
          className={cn(
            "flex items-center gap-2 border-b-2 px-3 py-2 text-sm font-medium transition-colors",
            tab === "documents"
              ? "border-primary text-foreground"
              : "border-transparent text-muted-foreground hover:text-foreground"
          )}
        >
          <FileText className="size-4" />
          Organization Documents
        </button>
      </div>

      {tab === "members" ? (
        <MembersPanel
          organizationId={organizationId}
          domain={domain}
          isAdmin={isAdmin}
          currentUserId={currentUserId}
        />
      ) : (
        <OrganizationDocumentsPanel
          organizationId={organizationId}
          canManage={isAdmin}
          canView={isAdmin || hasCompanyAccess}
        />
      )}
    </main>
  );
}
