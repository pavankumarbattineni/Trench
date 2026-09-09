/**
 * Trench's monogram: three flush-left bars, each narrower than the last --
 * a descending, terraced profile standing in for the app's namesake (depth,
 * strata, going further down), rather than a literal typographic "T".
 */
export function TrenchMark({ className }: { className?: string }) {
  return (
    <svg viewBox="0 0 40 40" className={className} aria-hidden="true">
      <rect x="6" y="7" width="28" height="6" rx="3" fill="currentColor" />
      <rect x="6" y="17" width="20" height="6" rx="3" fill="currentColor" />
      <rect x="6" y="27" width="12" height="6" rx="3" fill="currentColor" />
    </svg>
  );
}
