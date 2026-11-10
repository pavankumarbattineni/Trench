# syntax=docker/dockerfile:1
# Trench frontend: Next.js 16 (standalone output). Three stages:
# deps -> builder -> runner.

# Shared base: Node 24 LTS on Debian slim (Next 16 needs Node >= 20.9).
FROM node:24-slim AS base
# Opt out of Next.js anonymous telemetry during build and run.
ENV NEXT_TELEMETRY_DISABLED=1
WORKDIR /app

# ============================================================================
# Stage 1: deps (install node_modules exactly as package-lock.json says)
# ============================================================================
FROM base AS deps
# Only the manifests first -> this slow layer is cached until they change.
COPY package.json package-lock.json ./
# npm ci = clean, lockfile-exact install. The cache mount keeps npm's
# download cache between builds, outside the image.
RUN --mount=type=cache,target=/root/.npm npm ci

# ============================================================================
# Stage 2: builder (compile the app with `next build`)
# ============================================================================
FROM base AS builder
COPY --from=deps /app/node_modules ./node_modules
COPY . .

# Public, browser-visible values baked into the JS bundle at build time.
# Build args are available as environment variables to RUN below.
ARG NEXT_PUBLIC_API_BASE_URL
ARG NEXT_PUBLIC_FIREBASE_API_KEY
ARG NEXT_PUBLIC_FIREBASE_AUTH_DOMAIN
ARG NEXT_PUBLIC_FIREBASE_PROJECT_ID
ARG NEXT_PUBLIC_FIREBASE_STORAGE_BUCKET
ARG NEXT_PUBLIC_FIREBASE_MESSAGING_SENDER_ID
ARG NEXT_PUBLIC_FIREBASE_APP_ID

RUN npm run build

# ============================================================================
# Stage 3: runner (only what's needed to serve the app)
# ============================================================================
FROM base AS runner
# PORT/HOSTNAME are read by the standalone server.js. 0.0.0.0 = listen on
# all interfaces so the published port is reachable from outside.
ENV NODE_ENV=production \
    PORT=3000 \
    HOSTNAME=0.0.0.0

# Standalone server + traced node_modules, plus the two folders it doesn't
# include by itself (public assets and hashed JS/CSS chunks). Owned by the
# non-root "node" user so Next can write its runtime cache (.next/cache).
COPY --from=builder --chown=node:node /app/.next/standalone ./
COPY --from=builder --chown=node:node /app/.next/static ./.next/static
COPY --from=builder --chown=node:node /app/public ./public

USER node
EXPOSE 3000

# Healthy = the server answers a page request (redirects are followed).
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
  CMD ["node", "-e", "fetch('http://127.0.0.1:' + (process.env.PORT || 3000) + '/').then(r => process.exit(r.ok ? 0 : 1)).catch(() => process.exit(1))"]

# Plain `node server.js` (not `npm start`): node is process 1 and receives
# Docker's stop signal directly for a clean shutdown.
CMD ["node", "server.js"]
