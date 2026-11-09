import type { Metadata } from "next";
import "./globals.css";

import { Providers } from "@/components/providers";

// Fonts load straight from Google Fonts (display=swap) rather than through
// next/font/google: Google serves some font files from dynamic
// "fonts.gstatic.com/l/font?kit=..." URLs, which Turbopack's next/font loader
// can't resolve ("next/font/google queries have exactly one entry"), and
// which font gets that URL varies between requests. The families are applied
// via the font-sans / font-serif / font-mono tokens in globals.css.
//   Inter 400/500/600          -- app default (UI, user messages, sidebar)
//   Source Serif 4 400/600     -- assistant responses
//   JetBrains Mono 400/500     -- inline code and code blocks
const GOOGLE_FONTS_URL =
  "https://fonts.googleapis.com/css2" +
  "?family=Inter:wght@400;500;600" +
  "&family=JetBrains+Mono:wght@400;500" +
  "&family=Source+Serif+4:wght@400;600" +
  "&display=swap";

export const metadata: Metadata = {
  title: "Trench",
  description: "Your personal knowledge base.",
};

export default function RootLayout({ children }: LayoutProps<"/">) {
  return (
    <html lang="en" suppressHydrationWarning className="h-full antialiased">
      <head>
        <link rel="preconnect" href="https://fonts.googleapis.com" />
        <link rel="preconnect" href="https://fonts.gstatic.com" crossOrigin="anonymous" />
        <link rel="stylesheet" href={GOOGLE_FONTS_URL} />
      </head>
      {/* suppressHydrationWarning: some browser extensions inject their own
          attributes (e.g. a "rtrvr-ls" marker) into <body> before React
          hydrates, which otherwise falsely reports as a hydration mismatch
          -- see https://react.dev/link/hydration-mismatch. */}
      <body className="min-h-full flex flex-col" suppressHydrationWarning>
        <Providers>{children}</Providers>
      </body>
    </html>
  );
}
