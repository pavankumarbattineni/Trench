"use client";

import { useRouter } from "next/navigation";

import { useAuth } from "@/components/auth-provider";
import type { UserProfile } from "@/lib/api";

/**
 * "Authenticated just now" handler: records the profile in AuthProvider's
 * state and goes straight to the main chat page. Used on /signin (both
 * email/password and "Continue with Google") and on /invite/accept --
 * not on /signup, since Owner signup issues no session (the backend's
 * POST /auth/signup/owner returns tenant info only; the Owner signs in
 * separately afterward).
 */
export function useAuthSuccess() {
  const router = useRouter();
  const { setUser } = useAuth();

  return (profile: UserProfile) => {
    setUser(profile);
    router.push("/chat");
  };
}
