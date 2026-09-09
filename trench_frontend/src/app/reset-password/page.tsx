import { Suspense } from "react";

import { AuthCard } from "@/components/auth-card";

import { ResetPasswordForm } from "./reset-password-form";

export default function ResetPasswordPage() {
  return (
    <Suspense
      fallback={
        <AuthCard title="Reset your password">
          <p className="text-sm text-muted-foreground">Loading…</p>
        </AuthCard>
      }
    >
      <ResetPasswordForm />
    </Suspense>
  );
}
