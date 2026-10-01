import { Suspense } from "react";

import { AuthCard } from "@/components/auth-card";

import { AcceptInvitationForm } from "./accept-invitation-form";

export default function InviteAcceptPage() {
  return (
    <Suspense
      fallback={
        <AuthCard title="Accept invitation">
          <p className="text-sm text-muted-foreground">Loading…</p>
        </AuthCard>
      }
    >
      <AcceptInvitationForm />
    </Suspense>
  );
}
