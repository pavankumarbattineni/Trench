"use client";

import { zodResolver } from "@hookform/resolvers/zod";
import Link from "next/link";
import { useState } from "react";
import { useForm, useWatch } from "react-hook-form";
import { z } from "zod";

import { AuthCard } from "@/components/auth-card";
import { AuthDivider } from "@/components/auth-divider";
import { GoogleSignInButton } from "@/components/google-signin-button";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { PasswordInput } from "@/components/ui/password-input";
import { signUpOwner } from "@/lib/auth-service";
import { getErrorMessage } from "@/lib/errors";

// Mirrors the backend's OwnerSignupRequest.username constraint
// (app/schemas/auth.py) -- kept in sync deliberately, not shared code,
// since the backend re-validates independently regardless of this check.
const schema = z
  .object({
    username: z
      .string()
      .min(3, "At least 3 characters")
      .max(32, "At most 32 characters")
      .regex(/^[a-zA-Z0-9_-]+$/, "Letters, numbers, - and _ only"),
    organizationName: z
      .string()
      .min(1, "Required")
      .max(128, "At most 128 characters"),
    email: z
      .string()
      .email("Enter a valid email address")
      .refine((email) => !isPersonalEmailDomain(email), {
        message: "Please use a business email address.",
      }),
    password: z.string().min(8, "At least 8 characters"),
    confirmPassword: z.string(),
  })
  .refine((data) => data.password === data.confirmPassword, {
    message: "Passwords don't match",
    path: ["confirmPassword"],
  });

type FormValues = z.infer<typeof schema>;

// Client-side UX hint only, not exhaustive -- the backend independently
// re-validates against its own (larger) blocklist at signup time
// regardless of what this check allows through.
const PERSONAL_EMAIL_DOMAINS = new Set([
  "gmail.com",
  "googlemail.com",
  "yahoo.com",
  "outlook.com",
  "hotmail.com",
  "live.com",
  "icloud.com",
  "me.com",
  "aol.com",
  "protonmail.com",
]);

function isPersonalEmailDomain(email: string): boolean {
  const domain = email.split("@")[1]?.toLowerCase();
  return Boolean(domain && PERSONAL_EMAIL_DOMAINS.has(domain));
}

export default function SignupPage() {
  const [formError, setFormError] = useState<string | null>(null);
  const [created, setCreated] = useState(false);

  const {
    register,
    handleSubmit,
    control,
    formState: { errors, isSubmitting },
  } = useForm<FormValues>({ resolver: zodResolver(schema) });
  const organizationName = useWatch({ control, name: "organizationName" }) ?? "";

  const onSubmit = async (values: FormValues) => {
    setFormError(null);
    try {
      await signUpOwner(
        values.username,
        values.email,
        values.password,
        values.organizationName
      );
      setCreated(true);
    } catch (error) {
      setFormError(getErrorMessage(error));
    }
  };

  if (created) {
    return (
      <AuthCard
        title="Organization created"
        subtitle="Sign in to continue."
        footer={
          <Link
            href="/signin"
            className="font-medium text-foreground underline underline-offset-4"
          >
            Go to sign in
          </Link>
        }
      >
        <div />
      </AuthCard>
    );
  }

  return (
    <AuthCard
      title="Create your organization"
      subtitle="You'll be the owner — invite your team once you're in."
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
        organizationName={organizationName}
        onSuccess={() => setCreated(true)}
        onError={setFormError}
      />

      <AuthDivider />

      <form onSubmit={handleSubmit(onSubmit)} className="space-y-4" noValidate>
        <div className="space-y-2">
          <Label htmlFor="organizationName">Organization name</Label>
          <Input
            id="organizationName"
            autoComplete="organization"
            aria-invalid={Boolean(errors.organizationName)}
            {...register("organizationName")}
          />
          {errors.organizationName && (
            <p className="text-sm text-destructive">
              {errors.organizationName.message}
            </p>
          )}
        </div>
        <div className="space-y-2">
          <Label htmlFor="username">Username</Label>
          <Input
            id="username"
            autoComplete="username"
            aria-invalid={Boolean(errors.username)}
            {...register("username")}
          />
          {errors.username && (
            <p className="text-sm text-destructive">{errors.username.message}</p>
          )}
        </div>
        <div className="space-y-2">
          <Label htmlFor="email">Work email</Label>
          <Input
            id="email"
            type="email"
            autoComplete="email"
            aria-invalid={Boolean(errors.email)}
            {...register("email")}
          />
          {errors.email && (
            <p className="text-sm text-destructive">{errors.email.message}</p>
          )}
        </div>
        <div className="space-y-2">
          <Label htmlFor="password">Password</Label>
          <PasswordInput
            id="password"
            autoComplete="new-password"
            aria-invalid={Boolean(errors.password)}
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
            aria-invalid={Boolean(errors.confirmPassword)}
            {...register("confirmPassword")}
          />
          {errors.confirmPassword && (
            <p className="text-sm text-destructive">{errors.confirmPassword.message}</p>
          )}
        </div>

        {formError && <p className="text-sm text-destructive">{formError}</p>}
        <Button type="submit" className="w-full" disabled={isSubmitting}>
          {isSubmitting ? "Creating organization…" : "Create organization"}
        </Button>
      </form>
    </AuthCard>
  );
}
