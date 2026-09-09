import { TrenchMark } from "@/components/trench-mark";

export function Logo({ className }: { className?: string }) {
  return (
    <div className={`flex items-center gap-3 ${className ?? ""}`}>
      <span className="flex h-10 w-10 items-center justify-center overflow-hidden rounded-xl bg-primary text-primary-foreground">
        <TrenchMark className="h-6 w-6" />
      </span>
      <span className="text-lg font-bold tracking-tight">Trench</span>
    </div>
  );
}
