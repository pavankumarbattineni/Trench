import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  // Emit a self-contained server (.next/standalone/server.js) with only the
  // node_modules files it actually uses -- small Docker images.
  // Vercel ignores this and keeps its own deployment format.
  output: "standalone",
};

export default nextConfig;
