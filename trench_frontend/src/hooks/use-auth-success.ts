"use client";

import { useRouter } from "next/navigation";

import { useAuth } from "@/components/auth-provider";
import type { UserProfile } from "@/lib/api";

/**
 * "Signin just completed" handler: records the profile in AuthProvider's
 * state and goes straight to the main chat page. Signin-only -- signup
 * deliberately does NOT establish a session (see useSignupSuccess), so
 * this is used only on the /signin page, for both email/password and
 * "Continue with Google".
 */
export function useAuthSuccess() {
  const router = useRouter();
  const { setUser } = useAuth();

  return (profile: UserProfile) => {
    setUser(profile);
    router.push("/chat");
  };
}
