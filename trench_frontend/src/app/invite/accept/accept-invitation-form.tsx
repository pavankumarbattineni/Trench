"use client";

import { zodResolver } from "@hookform/resolvers/zod";
import { isAxiosError } from "axios";
import Link from "next/link";
import { useSearchParams } from "next/navigation";
import { useState } from "react";
import { useForm } from "react-hook-form";
import { createUserWithEmailAndPassword, signInWithEmailAndPassword } from "firebase/auth";
import { z } from "zod";

import { AuthCard } from "@/components/auth-card";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { PasswordInput } from "@/components/ui/password-input";
import { useAuthSuccess } from "@/hooks/use-auth-success";
import { getCurrentUser, persistTokens } from "@/lib/api";
import { getErrorMessage } from "@/lib/errors";
import { firebaseAuth } from "@/lib/firebase";
import { acceptInvitation } from "@/lib/invitations";

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

export function AcceptInvitationForm() {
  const searchParams = useSearchParams();
  const token = searchParams.get("token");
  const handleAuthSuccess = useAuthSuccess();
  const [formError, setFormError] = useState<string | null>(null);
  const [invalidTokenMessage, setInvalidTokenMessage] = useState<string | null>(
    token ? null : "This invitation link is missing or malformed."
  );

  const {
    register,
    handleSubmit,
    formState: { errors, isSubmitting },
  } = useForm<FormValues>({ resolver: zodResolver(schema) });

  const onSubmit = async (values: FormValues) => {
    if (!token) return;
    setFormError(null);

    try {
      // The invitee may be brand new to Firebase (the common case) or may
      // already have a Firebase identity from a prior attempt at this same
      // flow -- try creating the account first, and fall back to signing
      // in with the same credentials if Firebase says the email is
      // already registered, rather than making the person start over.
      let idToken: string;
      try {
        const credential = await createUserWithEmailAndPassword(
          firebaseAuth,
          values.email,
          values.password
        );
        idToken = await credential.user.getIdToken();
      } catch (error) {
        const code = (error as { code?: string } | undefined)?.code;
        if (code !== "auth/email-already-in-use") throw error;
        const credential = await signInWithEmailAndPassword(
          firebaseAuth,
          values.email,
          values.password
        );
        idToken = await credential.user.getIdToken();
      }

      const tokens = await acceptInvitation(token, idToken, values.username);
      persistTokens(tokens);
      handleAuthSuccess(await getCurrentUser());
    } catch (error) {
      if (isAxiosError(error) && error.response?.status === 404) {
        setInvalidTokenMessage(
          "This invitation link is no longer valid — ask your organization admin to resend it."
        );
        return;
      }
      // A 403 (email mismatch) carries the backend's own specific message
      // ("This invite was sent to X…"), which is more useful than any
      // generic copy we'd write here -- show it as-is.
      setFormError(getErrorMessage(error));
    }
  };

  if (invalidTokenMessage) {
    return (
      <AuthCard
        title="Invitation not valid"
        subtitle={invalidTokenMessage}
        footer={
          <Link
            href="/signin"
            className="font-medium text-foreground underline underline-offset-4"
          >
            Back to sign in
          </Link>
        }
      >
        <p className="text-sm text-muted-foreground">
          If you believe this is a mistake, ask whoever invited you to send a
          new invitation.
        </p>
      </AuthCard>
    );
  }

  return (
    <AuthCard
      title="Accept your invitation"
      subtitle="Create your account to join the organization."
    >
      <form onSubmit={handleSubmit(onSubmit)} className="space-y-4" noValidate>
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
          <Label htmlFor="email">Email</Label>
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
          <p className="text-xs text-muted-foreground">
            You must use the email address this invitation was sent to.
          </p>
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
          {isSubmitting ? "Joining…" : "Accept invitation"}
        </Button>
      </form>
    </AuthCard>
  );
}
