"use client";

import { createContext, useCallback, useContext, useEffect, useState } from "react";

import { getCurrentUser, isAuthenticated, type UserProfile } from "@/lib/api";

interface AuthContextValue {
  user: UserProfile | null;
  loading: boolean;
  refreshUser: () => Promise<void>;
  setUser: (user: UserProfile | null) => void;
}

const AuthContext = createContext<AuthContextValue | null>(null);

export function AuthProvider({ children }: { children: React.ReactNode }) {
  const [user, setUser] = useState<UserProfile | null>(null);
  const [loading, setLoading] = useState(true);

  const refreshUser = useCallback(async () => {
    // Skip the network call entirely when there's no access_token cookie
    // -- a fresh/logged-out visitor is the common case, not an error, and
    // hitting a protected endpoint anyway would 401, which the response
    // interceptor treats as "session died" and reacts to by redirecting
    // to /signin -- looping forever if we're already there.
    if (!isAuthenticated()) {
      setUser(null);
      return;
    }
    try {
      setUser(await getCurrentUser());
    } catch {
      setUser(null);
    }
  }, []);

  useEffect(() => {
    // Fetches the session once on mount; setUser/setLoading run inside the
    // promise's callbacks, not synchronously in the effect body.
    // eslint-disable-next-line react-hooks/set-state-in-effect
    refreshUser().finally(() => setLoading(false));
    // Only ever needs to run once, on mount.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  return (
    <AuthContext.Provider value={{ user, loading, refreshUser, setUser }}>
      {children}
    </AuthContext.Provider>
  );
}

export function useAuth(): AuthContextValue {
  const context = useContext(AuthContext);
  if (!context) {
    throw new Error("useAuth must be used within an AuthProvider");
  }
  return context;
}
