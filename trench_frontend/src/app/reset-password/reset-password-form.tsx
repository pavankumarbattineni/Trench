"use client";

import { zodResolver } from "@hookform/resolvers/zod";
import Link from "next/link";
import { useSearchParams } from "next/navigation";
import { useEffect, useState } from "react";
import { useForm } from "react-hook-form";
import { z } from "zod";

import { AuthCard } from "@/components/auth-card";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { completePasswordReset, verifyResetCode } from "@/lib/auth-service";
import { getErrorMessage } from "@/lib/errors";

const schema = z
  .object({
    password: z.string().min(8, "At least 8 characters"),
    confirmPassword: z.string(),
  })
  .refine((data) => data.password === data.confirmPassword, {
    message: "Passwords don't match",
    path: ["confirmPassword"],
  });

type FormValues = z.infer<typeof schema>;

type CodeState =
  | { status: "checking" }
  | { status: "valid"; email: string }
  | { status: "invalid"; message: string }
  | { status: "done" };

export function ResetPasswordForm() {
  const searchParams = useSearchParams();
  const oobCode = searchParams.get("oobCode");
  // The "missing code" case is knowable synchronously from the URL, so it's
  // computed as the initial state rather than set from an effect. Only the
  // "verify with Firebase" case is genuinely asynchronous.
  const [codeState, setCodeState] = useState<CodeState>(() =>
    oobCode
      ? { status: "checking" }
      : { status: "invalid", message: "This reset link is missing or malformed." }
  );
  const [formError, setFormError] = useState<string | null>(null);
  const {
    register,
    handleSubmit,
    formState: { errors, isSubmitting },
  } = useForm<FormValues>({ resolver: zodResolver(schema) });

  useEffect(() => {
    if (!oobCode) return;
    verifyResetCode(oobCode)
      .then((email) => setCodeState({ status: "valid", email }))
      .catch((error) =>
        setCodeState({ status: "invalid", message: getErrorMessage(error) })
      );
  }, [oobCode]);

  const onSubmit = async (values: FormValues) => {
    if (!oobCode) return;
    setFormError(null);
    try {
      await completePasswordReset(oobCode, values.password);
      setCodeState({ status: "done" });
    } catch (error) {
      setFormError(getErrorMessage(error));
    }
  };

  if (codeState.status === "checking") {
    return (
      <AuthCard title="Reset your password">
        <p className="text-sm text-muted-foreground">Checking your reset link…</p>
      </AuthCard>
    );
  }

  if (codeState.status === "invalid") {
    return (
      <AuthCard
        title="Link invalid"
        subtitle={codeState.message}
        footer={
          <Link
            href="/forgot-password"
            className="font-medium text-foreground underline underline-offset-4"
          >
            Request a new link
          </Link>
        }
      >
        <p className="text-sm text-muted-foreground">
          Reset links expire after a short time and can only be used once.
        </p>
      </AuthCard>
    );
  }

  if (codeState.status === "done") {
    return (
      <AuthCard
        title="Password updated"
        subtitle="You can now sign in with your new password."
        footer={
          <Link href="/signin" className="font-medium text-foreground underline underline-offset-4">
            Back to sign in
          </Link>
        }
      >
        <div />
      </AuthCard>
    );
  }

  return (
    <AuthCard title="Set a new password" subtitle={`Resetting the password for ${codeState.email}`}>
      <form onSubmit={handleSubmit(onSubmit)} className="space-y-4" noValidate>
        <div className="space-y-2">
          <Label htmlFor="password">New password</Label>
          <Input
            id="password"
            type="password"
            autoComplete="new-password"
            {...register("password")}
          />
          {errors.password && (
            <p className="text-sm text-destructive">{errors.password.message}</p>
          )}
        </div>
        <div className="space-y-2">
          <Label htmlFor="confirmPassword">Confirm new password</Label>
          <Input
            id="confirmPassword"
            type="password"
            autoComplete="new-password"
            {...register("confirmPassword")}
          />
          {errors.confirmPassword && (
            <p className="text-sm text-destructive">{errors.confirmPassword.message}</p>
          )}
        </div>
        {formError && <p className="text-sm text-destructive">{formError}</p>}
        <Button type="submit" className="w-full" disabled={isSubmitting}>
          {isSubmitting ? "Updating…" : "Update password"}
        </Button>
      </form>
    </AuthCard>
  );
}
