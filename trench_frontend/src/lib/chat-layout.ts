/**
 * The chat's reading column -- shared by the message list, the composer and
 * the stream-error line so their edges always line up.
 *
 * 48rem (768px) up to large screens, like ChatGPT/Claude; from 1280px wide
 * it grows to 56rem (896px) so wide monitors don't leave large empty
 * margins beside the sidebar, while keeping lines comfortably readable.
 */
export const CHAT_COLUMN = "mx-auto w-full max-w-3xl xl:max-w-4xl";

/**
 * Reserves the vertical scrollbar's width on both sides of a full-width
 * container. Applied to BOTH the message scroller and the composer's
 * wrapper so the two columns line up exactly at every width: without it
 * the scrollbar (messages only) shifts the message column half a
 * scrollbar-width left of the composer, or narrows it on small screens.
 * Overlay-scrollbar devices (phones, macOS) reserve nothing either way.
 */
export const COLUMN_GUTTER = "[scrollbar-gutter:stable_both-edges]";
