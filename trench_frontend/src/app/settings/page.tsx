import { ThemeToggle } from "@/components/theme-toggle";

export default function SettingsPage() {
  return (
    <main className="flex min-h-screen flex-col items-center justify-center gap-6 bg-background text-foreground">
      <h1 className="text-2xl font-bold">Settings</h1>
      <section className="flex flex-col items-center gap-2">
        <h2 className="text-sm uppercase tracking-wide text-muted-foreground">
          Theme
        </h2>
        <ThemeToggle />
      </section>
    </main>
  );
}
