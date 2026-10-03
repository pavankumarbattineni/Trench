"use client";

import { onAuthStateChanged, type User as FirebaseUser } from "firebase/auth";
import { useEffect, useState } from "react";

import { firebaseAuth } from "@/lib/firebase";

/**
 * Tracks the live Firebase Auth user client-side, distinct from Trench's own
 * backend session (see AuthProvider). Needed for provider-specific actions
 * (password reauthentication vs. Google reauthentication) that only Firebase
 * knows about. `ready` distinguishes "not signed in" from "still restoring
 * the persisted session" so callers don't briefly render as if there were no
 * linked providers at all.
 */
export function useFirebaseUser() {
  const [user, setUser] = useState<FirebaseUser | null>(firebaseAuth.currentUser);
  const [ready, setReady] = useState(false);

  useEffect(
    () =>
      onAuthStateChanged(firebaseAuth, (nextUser) => {
        setUser(nextUser);
        setReady(true);
      }),
    []
  );

  return {
    user,
    ready,
    hasPassword: Boolean(user?.providerData.some((p) => p.providerId === "password")),
    hasGoogle: Boolean(user?.providerData.some((p) => p.providerId === "google.com")),
  };
}
