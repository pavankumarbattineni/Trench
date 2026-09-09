"use client";

import { useRouter } from "next/navigation";

import { useAuth } from "@/components/auth-provider";
import type { UserProfile } from "@/lib/api";

/**
 * Shared "an auth action just completed" handler: records the profile in
 * AuthProvider's state and returns to the home page. Used identically after
 * email/password signup/signin and after Google sign-in, on both auth pages.
 */
export function useAuthSuccess() {
  const router = useRouter();
  const { setUser } = useAuth();

  return (profile: UserProfile) => {
    setUser(profile);
    router.push("/");
  };
}
