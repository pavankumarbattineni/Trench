"use client";

import { useRouter } from "next/navigation";
import { toast } from "sonner";

/**
 * "Signup just completed" handler: signup never establishes a session (no
 * AuthProvider state to set, no tokens issued), so this just confirms
 * success and sends the user to /signin to authenticate for the first
 * time. Used identically after email/password signup and "Continue with
 * Google" used as a signup action, on the /signup page.
 */
export function useSignupSuccess() {
  const router = useRouter();

  return () => {
    toast.success("Account created -- please sign in to continue.");
    router.push("/signin");
  };
}
