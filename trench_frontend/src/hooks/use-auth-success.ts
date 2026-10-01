"use client";

import { useRouter } from "next/navigation";

import { useAuth } from "@/components/auth-provider";
import type { UserProfile } from "@/lib/api";

/**
 * "Authenticated just now" handler: records the profile in AuthProvider's
 * state and goes straight to the main chat page. Used on /signin (both
 * email/password and "Continue with Google") and on /signup, since Owner
 * signup now establishes a session immediately rather than deferring to a
 * separate sign-in step.
 */
export function useAuthSuccess() {
  const router = useRouter();
  const { setUser } = useAuth();

  return (profile: UserProfile) => {
    setUser(profile);
    router.push("/chat");
  };
}
