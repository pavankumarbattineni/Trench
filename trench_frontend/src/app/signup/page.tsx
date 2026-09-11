"use client";

import { zodResolver } from "@hookform/resolvers/zod";
import { useQuery } from "@tanstack/react-query";
import Link from "next/link";
import { useState } from "react";
import { useForm } from "react-hook-form";
import { z } from "zod";

import { AuthCard } from "@/components/auth-card";
import { AuthDivider } from "@/components/auth-divider";
import { GoogleSignInButton } from "@/components/google-signin-button";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { PasswordInput } from "@/components/ui/password-input";
import { Switch } from "@/components/ui/switch";
import { useSignupSuccess } from "@/hooks/use-signup-success";
import { getAdminStatus } from "@/lib/api";
import { signUp } from "@/lib/auth-service";
import { getErrorMessage } from "@/lib/errors";

// Mirrors the backend's SignupRequest.username constraint
// (app/schemas/auth.py) -- kept in sync deliberately, not shared code,
// since the backend re-validates independently regardless of this check.
const schema = z
  .object({
    username: z
      .string()
      .min(3, "At least 3 characters")
      .max(32, "At most 32 characters")
      .regex(/^[a-zA-Z0-9_-]+$/, "Letters, numbers, - and _ only"),
    email: z.string().email("Enter a valid email address"),
    password: z.string().min(8, "At least 8 characters"),
    confirmPassword: z.string(),
  })
  .refine((data) => data.password === data.confirmPassword, {
    message: "Passwords don't match",
    path: ["confirmPassword"],
  });

type FormValues = z.infer<typeof schema>;

export default function SignupPage() {
  const handleSignupSuccess = useSignupSuccess();
  const [formError, setFormError] = useState<string | null>(null);
  // Only ever offered while no Trench administrator exists yet -- purely
  // a UX convenience; the backend independently re-checks the same
  // "no admin yet" condition when signup actually happens, so hiding this
  // is never the security boundary.
  const [registerAsAdmin, setRegisterAsAdmin] = useState(false);
  const adminStatusQuery = useQuery({
    queryKey: ["auth", "admin-status"],
    queryFn: getAdminStatus,
  });
  const offerAdminToggle = adminStatusQuery.data === false;

  const {
    register,
    handleSubmit,
    formState: { errors, isSubmitting },
  } = useForm<FormValues>({ resolver: zodResolver(schema) });

  const onSubmit = async (values: FormValues) => {
    setFormError(null);
    try {
      await signUp(values.username, values.email, values.password, registerAsAdmin);
      handleSignupSuccess();
    } catch (error) {
      setFormError(getErrorMessage(error));
    }
  };

  return (
    <AuthCard
      title="Create your account"
      subtitle="Start building your knowledge base."
      footer={
        <>
          Already have an account?{" "}
          <Link
            href="/signin"
            className="font-medium text-foreground underline underline-offset-4"
          >
            Sign in
          </Link>
        </>
      }
    >
      <GoogleSignInButton
        mode="signup"
        registerAsAdmin={registerAsAdmin}
        onSuccess={handleSignupSuccess}
        onError={setFormError}
      />

      <AuthDivider />

      <form onSubmit={handleSubmit(onSubmit)} className="space-y-4" noValidate>
        <div className="space-y-2">
          <Label htmlFor="username">Username</Label>
          <Input id="username" autoComplete="username" {...register("username")} />
          {errors.username && (
            <p className="text-sm text-destructive">{errors.username.message}</p>
          )}
        </div>
        <div className="space-y-2">
          <Label htmlFor="email">Email</Label>
          <Input id="email" type="email" autoComplete="email" {...register("email")} />
          {errors.email && (
            <p className="text-sm text-destructive">{errors.email.message}</p>
          )}
        </div>
        <div className="space-y-2">
          <Label htmlFor="password">Password</Label>
          <PasswordInput
            id="password"
            autoComplete="new-password"
            {...register("password")}
          />
          {errors.password && (
            <p className="text-sm text-destructive">{errors.password.message}</p>
          )}
        </div>
        <div className="space-y-2">
          <Label htmlFor="confirmPassword">Confirm password</Label>
          <PasswordInput
            id="confirmPassword"
            autoComplete="new-password"
            {...register("confirmPassword")}
          />
          {errors.confirmPassword && (
            <p className="text-sm text-destructive">{errors.confirmPassword.message}</p>
          )}
        </div>

        {offerAdminToggle && (
          <div className="flex items-start justify-between gap-3 rounded-xl border border-border bg-muted/40 p-3">
            <div className="space-y-0.5">
              <Label htmlFor="register-as-admin">Register as administrator</Label>
              <p className="text-xs text-muted-foreground">
                No Trench administrator exists yet. Only an administrator can
                create organizations.
              </p>
            </div>
            <Switch
              id="register-as-admin"
              checked={registerAsAdmin}
              onCheckedChange={setRegisterAsAdmin}
            />
          </div>
        )}

        {formError && <p className="text-sm text-destructive">{formError}</p>}
        <Button type="submit" className="w-full" disabled={isSubmitting}>
          {isSubmitting ? "Creating account…" : "Create account"}
        </Button>
      </form>
    </AuthCard>
  );
}
