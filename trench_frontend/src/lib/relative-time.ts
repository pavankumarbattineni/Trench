/**
 * One timestamp format for the whole app (message rows and thread
 * tooltips), so the same moment always reads the same way:
 *
 * - Earlier calendar day (local time) -> the date, e.g. "Nov 8, 2026".
 *   Switches as soon as the day changes -- a message from 11:50 PM shows
 *   its date at 12:05 AM, not "15 min ago".
 * - Same day, under 2 minutes       -> "Just now"
 * - Same day, under an hour          -> "N min ago" (starting at "2 min ago")
 * - Same day, otherwise              -> "1 hour ago" / "N hours ago"
 */

const DATE_FORMAT = new Intl.DateTimeFormat("en-US", {
  month: "short",
  day: "numeric",
  year: "numeric",
});

const DATE_TIME_FORMAT = new Intl.DateTimeFormat("en-US", {
  month: "short",
  day: "numeric",
  year: "numeric",
  hour: "numeric",
  minute: "2-digit",
});

const MINUTE_MS = 60_000;
const HOUR_MS = 60 * MINUTE_MS;
// "Just now" covers the first two minutes, so the first relative label is
// "2 min ago" (never "1 min ago").
const JUST_NOW_MS = 2 * MINUTE_MS;

function isSameCalendarDay(a: Date, b: Date): boolean {
  return (
    a.getFullYear() === b.getFullYear() &&
    a.getMonth() === b.getMonth() &&
    a.getDate() === b.getDate()
  );
}

export function formatRelativeTime(value: string | Date, now: Date = new Date()): string {
  const date = value instanceof Date ? value : new Date(value);
  if (Number.isNaN(date.getTime())) return "";

  if (!isSameCalendarDay(date, now) && date < now) {
    return DATE_FORMAT.format(date);
  }

  // A slightly-ahead server clock (negative elapsed) still reads "Just now".
  const elapsed = now.getTime() - date.getTime();
  if (elapsed < JUST_NOW_MS) return "Just now";
  if (elapsed < HOUR_MS) return `${Math.floor(elapsed / MINUTE_MS)} min ago`;
  const hours = Math.floor(elapsed / HOUR_MS);
  return `${hours} ${hours === 1 ? "hour" : "hours"} ago`;
}

/** Exact local date and time, for a timestamp's hover title. */
export function formatFullDateTime(value: string | Date): string {
  const date = value instanceof Date ? value : new Date(value);
  return Number.isNaN(date.getTime()) ? "" : DATE_TIME_FORMAT.format(date);
}
