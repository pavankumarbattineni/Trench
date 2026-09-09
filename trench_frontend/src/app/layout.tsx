import type { Metadata } from "next";
import { Inter } from "next/font/google";
import "./globals.css";

import { Providers } from "@/components/providers";

// Approximates Claude's app typography (a clean, neutral grotesk sans) using
// a freely licensed equivalent -- Anthropic's own "AnthropicSans" webfont is
// a proprietary brand asset, not something to vendor into an unrelated app.
const inter = Inter({
  variable: "--font-sans",
  subsets: ["latin"],
});

export const metadata: Metadata = {
  title: "Trench",
  description: "Your personal knowledge base.",
};

export default function RootLayout({ children }: LayoutProps<"/">) {
  return (
    <html lang="en" suppressHydrationWarning className={`${inter.variable} h-full antialiased`}>
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
