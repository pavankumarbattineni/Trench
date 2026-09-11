"use client";

import { Suspense } from "react";
import { usePathname, useRouter, useSearchParams } from "next/navigation";
import { isAxiosError } from "axios";
import { Building2, FileText, Users } from "lucide-react";

import { ShieldAlert } from "lucide-react";

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
    // Only a Trench *application* admin (User.role -- entirely separate
    // from any organization's own admin role) can create an organization.
    // Backend-enforced (require_trench_admin on POST /organizations) --
    // this is purely so a regular user isn't shown a form they can't
    // submit.
    if (user?.role !== "admin") {
      return (
        <main className="mx-auto flex h-full max-w-3xl items-center justify-center px-4">
          <div className="flex max-w-md flex-col items-center gap-3 rounded-2xl border border-dashed border-border p-8 text-center">
            <ShieldAlert className="size-8 text-muted-foreground" />
            <p className="text-sm text-muted-foreground">
              You&apos;re not part of an organization yet, and only a Trench
              administrator can create one. Ask your administrator to create an
              organization and add you as a member.
            </p>
          </div>
        </main>
      );
    }
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

interface OrganizationDetailProps {
  organizationId: string;
  name: string;
  domain: string;
  createdAt: string;
  isAdmin: boolean;
  hasCompanyAccess: boolean;
  currentUserId: string | undefined;
}

function OrganizationDetail(props: OrganizationDetailProps) {
  // useSearchParams requires a Suspense boundary (Next.js opts a page
  // using it into client-only rendering otherwise) -- cheap to add and
  // keeps this correct regardless of how the route ends up being
  // rendered.
  return (
    <Suspense
      fallback={
        <main className="mx-auto flex h-full max-w-3xl flex-col gap-4 overflow-y-auto px-4 py-10">
          <Skeleton className="h-24 w-full rounded-2xl" />
          <Skeleton className="h-8 w-64" />
        </main>
      }
    >
      <OrganizationDetailContent {...props} />
    </Suspense>
  );
}

function OrganizationDetailContent({
  organizationId,
  name,
  domain,
  createdAt,
  isAdmin,
  hasCompanyAccess,
  currentUserId,
}: OrganizationDetailProps) {
  // The active tab lives in the URL (?tab=members|documents), not local
  // component state -- a component's own useState resets to its initial
  // value on every remount, which is exactly what a full page refresh
  // does, so a tab held only in memory always snaps back to "members" on
  // reload. Reading/writing it through the URL survives refreshes,
  // back/forward navigation, and sharing a direct link to a tab.
  const searchParams = useSearchParams();
  const router = useRouter();
  const pathname = usePathname();
  const tab: OrgTab = searchParams.get("tab") === "documents" ? "documents" : "members";

  const setTab = (next: OrgTab) => {
    const params = new URLSearchParams(searchParams.toString());
    params.set("tab", next);
    router.replace(`${pathname}?${params.toString()}`, { scroll: false });
  };

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
