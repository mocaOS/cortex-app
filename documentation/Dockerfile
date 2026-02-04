# =============================================================================
# MOCA Library Documentation
# Multi-stage Dockerfile for building and serving Zudoku documentation
# =============================================================================

# -----------------------------------------------------------------------------
# Stage 1: Dependencies
# -----------------------------------------------------------------------------
FROM node:20-alpine AS deps
WORKDIR /app

# Install dependencies
COPY package.json package-lock.json* ./
RUN npm ci --legacy-peer-deps

# -----------------------------------------------------------------------------
# Stage 2: Builder
# -----------------------------------------------------------------------------
FROM node:20-alpine AS builder
WORKDIR /app

# Build-time argument for docs URL (used in zudoku.config.tsx)
ARG DOCS_URL=http://localhost:3000
ENV DOCS_URL=${DOCS_URL}

# Copy dependencies from deps stage
COPY --from=deps /app/node_modules ./node_modules

# Copy source files
COPY . .

# Build the documentation
RUN npm run build

# -----------------------------------------------------------------------------
# Stage 3: Production Runner
# -----------------------------------------------------------------------------
FROM node:20-alpine AS runner
WORKDIR /app

# Set production environment
ENV NODE_ENV=production

# Install serve for static file serving (as root, before switching users)
RUN npm install -g serve@14

# Create non-root user for security
RUN addgroup --system --gid 1001 nodejs && \
    adduser --system --uid 1001 zudoku

# Copy built files from builder stage
COPY --from=builder --chown=zudoku:nodejs /app/dist ./dist

# Switch to non-root user
USER zudoku

# Expose port 3000
EXPOSE 3000

# Health check
HEALTHCHECK --interval=30s --timeout=10s --start-period=5s --retries=3 \
  CMD wget --no-verbose --tries=1 --spider http://localhost:3000 || exit 1

# Serve the static files
CMD ["serve", "-s", "dist", "-l", "3000"]
