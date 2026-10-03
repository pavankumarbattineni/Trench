"use client";

import { useEffect, useState } from "react";

import { useQuery } from "@tanstack/react-query";
import Link from "next/link";

import { useAuth } from "@/components/auth-provider";
import { TrenchMark } from "@/components/trench-mark";
import { Button } from "@/components/ui/button";
import { getHealth } from "@/lib/api";

const HERO_MESSAGES = {
  company: {
    heading: "Your team's knowledge, all in one place.",
    body: "A shared knowledge base for your organization's documents, policies, and know-how — accessible to everyone who needs it.",
  },
  personal: {
    heading: "Keep everything you know in reach.",
    body: "A personal knowledge base for your notes, research, and ideas — built to grow with you.",
  },
} as const;

type HeroKind = keyof typeof HERO_MESSAGES;

const HERO_INTERVAL_MS = 5000;

// Alternates personal/company every HERO_INTERVAL_MS -- starts on personal.
// A plain setInterval, not a library: the project has no animation
// dependency, and a timer + CSS opacity transition is all this needs.
function useAlternatingHero(): HeroKind {
  const [active, setActive] = useState<HeroKind>("personal");

  useEffect(() => {
    const id = setInterval(() => {
      setActive((prev) => (prev === "company" ? "personal" : "company"));
    }, HERO_INTERVAL_MS);
    return () => clearInterval(id);
  }, []);

  return active;
}

export default function Home() {
  const { data, isError } = useQuery({
    queryKey: ["health"],
    queryFn: getHealth,
  });
  const { user, loading: authLoading } = useAuth();
  const activeHero = useAlternatingHero();

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
              <Button nativeButton={false} render={<Link href="/chat">Go to chat</Link>} />
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
        <div className="grid max-w-xl">
          {(Object.keys(HERO_MESSAGES) as HeroKind[]).map((kind) => (
            <div
              key={kind}
              aria-hidden={activeHero !== kind}
              className={`col-start-1 row-start-1 space-y-3 transition-opacity duration-700 motion-reduce:transition-none ${
                activeHero === kind ? "opacity-100" : "opacity-0"
              }`}
            >
              <h1 className="text-4xl font-bold tracking-tight sm:text-5xl">
                {HERO_MESSAGES[kind].heading}
              </h1>
              <p className="text-balance text-base text-muted-foreground sm:text-lg">
                {HERO_MESSAGES[kind].body}
              </p>
            </div>
          ))}
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
