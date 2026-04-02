# =============================================================================
# Trading Platform — Dashboard Dockerfile
#
# Multi-stage build:
#   Stage 1: Node 20 — compile TypeScript, build React bundle
#   Stage 2: nginx:alpine — serve static files + reverse proxy to backend
# =============================================================================

# =============================================================================
# Stage 1: Build
# =============================================================================
FROM node:20-alpine AS builder

WORKDIR /build

# Copy package manifest and lockfile
COPY dashboard/package.json dashboard/package-lock.json ./

# Install dependencies (--prefer-offline for faster builds in CI)
RUN npm ci --prefer-offline

# Copy source code and build config
COPY dashboard/ ./

# Build the React TypeScript application
# Output: dist/ with optimized bundles, CSS, assets
RUN npm run build

# =============================================================================
# Stage 2: Runtime
# =============================================================================
FROM nginx:alpine

# Copy nginx configuration (reverse proxy for backend API and WebSocket)
COPY dashboard/nginx.conf /etc/nginx/nginx.conf

# Copy built React SPA from builder stage
COPY --from=builder /build/dist /usr/share/nginx/html

# Create a non-root user for nginx (alpine already has 'nginx' user)
# Add health check for container orchestration
HEALTHCHECK --interval=30s --timeout=10s --start-period=10s --retries=2 \
    CMD wget --quiet --tries=1 --spider http://localhost/health || exit 1

# Expose port for HTTP traffic
EXPOSE 80

# nginx runs in foreground by default in the alpine image
CMD ["nginx", "-g", "daemon off;"]
