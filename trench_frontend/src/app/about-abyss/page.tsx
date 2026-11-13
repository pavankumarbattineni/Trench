import type { Metadata } from "next";
import Link from "next/link";

import { TrenchMark } from "@/components/trench-mark";
import { Button } from "@/components/ui/button";

const ABYSS_URL = "https://abyss-fleet.vercel.app";

export const metadata: Metadata = {
  title: "About Abyss | Trench",
  description:
    "Abyss, Trench's sibling app: build AI agents, connect real tools, and keep humans in control.",
};

const FEATURES = [
  {
    title: "Agents with real tools",
    body: "Describe what an agent does, then connect the tools it needs through MCP. No code, no infrastructure.",
  },
  {
    title: "Specialists that work together",
    body: "A main agent hands parts of a task to up to five specialists, who work in parallel and report back.",
  },
  {
    title: "You stay in control",
    body: "Sensitive tools wait for your approval, and every step of the agent's reasoning is there to inspect.",
  },
];

/** The two apps solve different problems; this says which one to reach for. */
const COMPARISON = [
  { need: "Get answers from documents you already have", app: "Trench" },
  { need: "Know exactly where each answer came from", app: "Trench" },
  { need: "Have an agent carry out work with real tools", app: "Abyss" },
  { need: "Split a task across specialists and approve risky steps", app: "Abyss" },
];

/** Abyss's own mark ("the descent": a ring, a disc sunk low inside it, and a
 * Tomato point at the bottom), in Abyss's colours so it reads as that app. */
function AbyssMark({ className }: { className?: string }) {
  return (
    <svg viewBox="0 0 40 40" className={className} aria-hidden="true">
      <circle cx="20" cy="18.5" r="11.5" fill="none" stroke="#1d2719" strokeWidth="3.6" />
      <circle cx="20" cy="22" r="6.4" fill="#1d2719" />
      <circle cx="20" cy="24.2" r="2.3" fill="#e8412c" />
    </svg>
  );
}

export default function AboutAbyssPage() {
  return (
    <main className="flex min-h-screen flex-col bg-background text-foreground">
      <header className="flex items-center justify-between px-6 py-6 sm:px-10">
        <Link href="/home" className="flex items-center gap-3 rounded-lg" aria-label="Trench home">
          <span className="flex h-9 w-9 items-center justify-center overflow-hidden rounded-lg bg-primary text-primary-foreground">
            <TrenchMark className="h-5 w-5" />
          </span>
          <span className="text-lg font-bold tracking-tight">Trench</span>
        </Link>
      </header>

      <div className="mx-auto w-full max-w-5xl flex-1 px-6 sm:px-10">
        <section className="grid items-center gap-10 py-12 sm:py-16 md:grid-cols-[1.4fr_1fr]">
          <div>
            <h1 className="text-4xl font-bold tracking-tight sm:text-5xl">
              Abyss: AI agents that do the work, with you in control.
            </h1>
            <p className="mt-5 max-w-xl text-base text-muted-foreground sm:text-lg">
              Where Trench answers questions from your documents, Abyss builds agents that act:
              they use your tools, share tasks between specialists, and pause for your approval
              before anything sensitive. Built by the same team as Trench.
            </p>
            <div className="mt-8 flex flex-wrap gap-3">
              <Button
                nativeButton={false}
                render={
                  <a href={ABYSS_URL} target="_blank" rel="noopener noreferrer">
                    Open Abyss
                    <span className="sr-only"> (opens in a new tab)</span>
                  </a>
                }
              />
              <Button
                variant="outline"
                nativeButton={false}
                render={<Link href="/home">Back to Trench</Link>}
              />
            </div>
          </div>
          <div className="flex justify-center md:justify-end">
            <span className="flex size-40 items-center justify-center rounded-[2.5rem] bg-[#9caf88] sm:size-52">
              <AbyssMark className="size-[78%]" />
            </span>
          </div>
        </section>

        <section aria-labelledby="abyss-features" className="border-t border-border py-12">
          <h2 id="abyss-features" className="sr-only">
            What Abyss does
          </h2>
          <div className="grid gap-8 md:grid-cols-3">
            {FEATURES.map(({ title, body }) => (
              <div key={title}>
                <h3 className="text-lg font-semibold">{title}</h3>
                <p className="mt-2 text-sm leading-6 text-muted-foreground">{body}</p>
              </div>
            ))}
          </div>
        </section>

        <section aria-labelledby="which-app" className="border-t border-border py-12">
          <h2 id="which-app" className="text-2xl font-bold tracking-tight">
            Which one do you need?
          </h2>
          <div className="mt-6 overflow-x-auto rounded-xl border border-border">
            <table className="w-full min-w-[28rem] text-left text-sm">
              <thead className="bg-muted text-muted-foreground">
                <tr>
                  <th scope="col" className="px-4 py-3 font-medium">If you want to…</th>
                  <th scope="col" className="w-32 px-4 py-3 font-medium">Use</th>
                </tr>
              </thead>
              <tbody>
                {COMPARISON.map(({ need, app }) => (
                  <tr key={need} className="border-t border-border">
                    <td className="px-4 py-3">{need}</td>
                    <td className="px-4 py-3 font-semibold">{app}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </section>
      </div>

      <footer className="px-6 py-6 text-center sm:px-10">
        <Link
          href="/home"
          className="text-xs text-muted-foreground underline underline-offset-4 hover:text-foreground"
        >
          Back to Trench
        </Link>
      </footer>
    </main>
  );
}
