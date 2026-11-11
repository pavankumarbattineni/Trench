"use client";

import { Suspense, useState } from "react";
import { usePathname, useRouter, useSearchParams } from "next/navigation";
import { isAxiosError } from "axios";
import { Building2, FileText, Mail, Trash2, Users } from "lucide-react";

import { useAuth } from "@/components/auth-provider";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/skeleton";
import { cn } from "cn";
import { useMyTenant } from "@/hooks/use-tenant";
import { getErrorMessage } from "@/lib/errors";
import { DeleteTenantDialog } from "./delete-tenant-dialog";
import { InvitationsTab } from "./invitations-tab";
import { MembersPanel } from "./members-panel";
import { TenantDocumentsPanel } from "./tenant-documents-panel";

type TenantTab = "invitations" | "members" | "documents";

export default function TenantPage() {
  const { user } = useAuth();
  const tenantQuery = useMyTenant();

  if (tenantQuery.isLoading) {
    return (
      <main className="mx-auto flex h-full max-w-3xl flex-col gap-4 overflow-y-auto px-4 py-10">
        <Skeleton className="h-8 w-48" />
        <Skeleton className="h-24 w-full rounded-2xl" />
        <Skeleton className="h-16 w-full rounded-xl" />
        <Skeleton className="h-16 w-full rounded-xl" />
      </main>
    );
  }

  // 404 = the user belongs to no tenant: they were removed from one, or
  // its Owner deleted it. Their account still works (personal knowledge,
  // chats); joining a tenant again takes a new invitation.
  if (isAxiosError(tenantQuery.error) && tenantQuery.error.response?.status === 404) {
    return (
      <main className="mx-auto flex h-full max-w-3xl items-center justify-center px-4">
        <div className="flex max-w-md flex-col items-center gap-3 rounded-2xl border border-border bg-card p-8 text-center">
          <span className="flex size-12 items-center justify-center rounded-xl bg-muted text-muted-foreground">
            <Building2 className="size-6" />
          </span>
          <h1 className="text-lg font-semibold tracking-tight">You&apos;re not part of a tenant</h1>
          <p className="text-sm text-muted-foreground">
            Your account, personal documents and chats are still available. To join a tenant, ask
            its owner or an admin to send you an invitation.
          </p>
        </div>
      </main>
    );
  }

  if (tenantQuery.isError || !tenantQuery.data) {
    return (
      <main className="mx-auto flex h-full max-w-3xl items-center justify-center px-4 text-center">
        <p className="text-sm text-destructive">{getErrorMessage(tenantQuery.error)}</p>
      </main>
    );
  }

  const tenant = tenantQuery.data;
  const isOwner = tenant.role === "owner";
  const isAdmin = isOwner || tenant.role === "admin";

  return (
    <TenantDetail
      tenantId={tenant.id}
      name={tenant.name}
      domain={tenant.domain}
      createdAt={tenant.created_at}
      isAdmin={isAdmin}
      isOwner={isOwner}
      hasCompanyAccess={Boolean(user?.has_company_access)}
      currentUserId={user?.id}
    />
  );
}

interface TenantDetailProps {
  tenantId: string;
  name: string;
  domain: string;
  createdAt: string;
  isAdmin: boolean;
  isOwner: boolean;
  hasCompanyAccess: boolean;
  currentUserId: string | undefined;
}

function TenantDetail(props: TenantDetailProps) {
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
      <TenantDetailContent {...props} />
    </Suspense>
  );
}

function TenantDetailContent({
  tenantId,
  name,
  domain,
  createdAt,
  isAdmin,
  isOwner,
  hasCompanyAccess,
  currentUserId,
}: TenantDetailProps) {
  // The active tab lives in the URL (?tab=members|documents), not local
  // component state -- a component's own useState resets to its initial
  // value on every remount, which is exactly what a full page refresh
  // does, so a tab held only in memory always snaps back to "members" on
  // reload. Reading/writing it through the URL survives refreshes,
  // back/forward navigation, and sharing a direct link to a tab.
  const searchParams = useSearchParams();
  const router = useRouter();
  const pathname = usePathname();
  // "members" is the default landing tab -- anything other than an exact
  // "documents" or "invitations" match (including no param at all) falls
  // back to it.
  const rawTab = searchParams.get("tab");
  const tab: TenantTab =
    rawTab === "documents" ? "documents" : rawTab === "invitations" ? "invitations" : "members";

  const setTab = (next: TenantTab) => {
    const params = new URLSearchParams(searchParams.toString());
    params.set("tab", next);
    router.replace(`${pathname}?${params.toString()}`, { scroll: false });
  };
  const [deleteOpen, setDeleteOpen] = useState(false);

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
        {isOwner && (
          <Badge variant="default" data-icon="inline-start">
            Owner
          </Badge>
        )}
        {isAdmin && !isOwner && (
          <Badge variant="default" data-icon="inline-start">
            Admin
          </Badge>
        )}
        {/* Owner-only: the backend rejects anyone else too (403). */}
        {isOwner && (
          <Button
            variant="ghost"
            size="icon"
            onClick={() => setDeleteOpen(true)}
            aria-label="Delete tenant"
            title="Delete tenant"
            className="-my-1 shrink-0 text-muted-foreground hover:bg-destructive/10 hover:text-destructive"
          >
            <Trash2 className="size-4" />
          </Button>
        )}
      </div>
      {isOwner && (
        <DeleteTenantDialog
          tenantId={tenantId}
          tenantName={name}
          open={deleteOpen}
          onOpenChange={setDeleteOpen}
        />
      )}

      <div className="flex gap-1 border-b border-border">
        <button
          type="button"
          onClick={() => setTab("invitations")}
          className={cn(
            "flex items-center gap-2 border-b-2 px-3 py-2 text-sm font-medium transition-colors",
            tab === "invitations"
              ? "border-primary text-foreground"
              : "border-transparent text-muted-foreground hover:text-foreground"
          )}
        >
          <Mail className="size-4" />
          Invitations
        </button>
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
          Tenant Documents
        </button>
      </div>

      {tab === "invitations" ? (
        <InvitationsTab tenantId={tenantId} isAdmin={isAdmin} isOwner={isOwner} />
      ) : tab === "members" ? (
        <MembersPanel
          tenantId={tenantId}
          isAdmin={isAdmin}
          isOwner={isOwner}
          currentUserId={currentUserId}
        />
      ) : (
        <TenantDocumentsPanel
          tenantId={tenantId}
          canManage={isAdmin}
          canView={isAdmin || hasCompanyAccess}
        />
      )}
    </main>
  );
}
