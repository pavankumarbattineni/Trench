import { cn } from "cn";

interface TruncatedEmailProps {
  email: string;
  className?: string;
}

/**
 * An email address that always fits its container.
 *
 * When space runs out the local part shrinks, with an ellipsis, so
 * the domain stays readable: "battinenipava…@gmail.com". An unusually
 * long domain is capped at 60% of the width with its own ellipsis:
 * "battinen…@claude-delete-te…". The full address is
 * in `title` (hover / long-press) and in the accessible name.
 */
export function TruncatedEmail({ email, className }: TruncatedEmailProps) {
  const at = email.lastIndexOf("@");
  if (at <= 0) {
    return (
      <span className={cn("block min-w-0 truncate", className)} title={email}>
        {email}
      </span>
    );
  }
  const local = email.slice(0, at);
  const domain = email.slice(at);
  return (
    <span
      className={cn("flex min-w-0 max-w-full overflow-hidden", className)}
      title={email}
      aria-label={email}
    >
      {/* Takes whatever space the domain leaves, truncating with "…". */}
      <span className="min-w-0 truncate" aria-hidden="true">
        {local}
      </span>
      {/* Never shrinks for normal domains (@gmail.com); an unusually long
          one is capped at 60% of the width and gets its own "…". */}
      <span className="max-w-[60%] shrink-0 truncate" aria-hidden="true">
        {domain}
      </span>
    </span>
  );
}
