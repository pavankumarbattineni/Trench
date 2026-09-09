"use client";

import { useQuery } from "@tanstack/react-query";
import Link from "next/link";

import { useAuth } from "@/components/auth-provider";
import { TrenchMark } from "@/components/trench-mark";
import { Button } from "@/components/ui/button";
import { getHealth } from "@/lib/api";

export default function Home() {
  const { data, isError } = useQuery({
    queryKey: ["health"],
    queryFn: getHealth,
  });
  const { user, loading: authLoading } = useAuth();

  return (
    <main
      className="flex min-h-screen flex-col bg-background text-foreground"
      suppressHydrationWarning
    >
      <header className="flex items-center justify-between px-6 py-6 sm:px-10">
        <div className="flex items-center gap-3">
          <span className="flex h-9 w-9 items-center justify-center overflow-hidden rounded-lg bg-primary text-primary-foreground">
            <TrenchMark className="h-5 w-5" />
          </span>
          <span className="text-lg font-bold tracking-tight">Trench</span>
        </div>

        {!authLoading && (
          <nav className="flex items-center gap-2">
            {user ? (
              <Button
                nativeButton={false}
                render={<Link href="/settings">Go to settings</Link>}
              />
            ) : (
              <>
                <Button
                  variant="ghost"
                  nativeButton={false}
                  render={<Link href="/signin">Sign in</Link>}
                />
                <Button
                  nativeButton={false}
                  render={<Link href="/signup">Create account</Link>}
                />
              </>
            )}
          </nav>
        )}
      </header>

      <section
        className="flex flex-1 flex-col items-center justify-center gap-8 px-6 text-center"
        suppressHydrationWarning
      >
        <span className="flex h-20 w-20 items-center justify-center overflow-hidden rounded-2xl bg-primary text-primary-foreground">
          <TrenchMark className="h-11 w-11" />
        </span>
        <div className="max-w-xl space-y-3">
          <h1 className="text-4xl font-bold tracking-tight sm:text-5xl">
            Keep everything you know in reach.
          </h1>
          <p className="text-balance text-base text-muted-foreground sm:text-lg">
            A personal knowledge base for your notes, research, and ideas — built to
            grow with you.
          </p>
        </div>
      </section>

      <footer className="px-6 py-6 text-center sm:px-10">
        <p className="text-xs text-muted-foreground">
          {isError ? (
            "Backend unreachable"
          ) : (
            <>
              System status: <span className="text-foreground">{data?.status ?? "…"}</span>
            </>
          )}
        </p>
      </footer>
    </main>
  );
}
