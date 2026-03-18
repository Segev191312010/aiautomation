# =============================================================================
# Stage 1 — Build the React dashboard
# =============================================================================
FROM node:20-alpine AS frontend-builder

WORKDIR /build/dashboard

# Install dependencies first (better layer caching)
COPY dashboard/package.json dashboard/package-lock.json* ./
RUN npm ci --prefer-offline

# Copy source and build
COPY dashboard/ ./
RUN npm run build


# =============================================================================
# Stage 2 — Python runtime
# =============================================================================
FROM python:3.11-slim AS runtime

# Non-root user for least-privilege execution
RUN groupadd --gid 1001 appgroup && \
    useradd --uid 1001 --gid appgroup --shell /bin/bash --create-home appuser

# Install curl (used by the Docker HEALTHCHECK below)
RUN apt-get update && \
    apt-get install -y --no-install-recommends curl && \
    rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Install Python dependencies (own layer — only rebuilds when requirements.txt changes)
COPY backend/requirements.txt ./requirements.txt
RUN pip install --no-cache-dir -r requirements.txt

# Copy backend source
COPY backend/ ./

# Copy the built React SPA from Stage 1 into the location FastAPI expects.
# config.py defaults DASHBOARD_BUILD_DIR to "../dashboard/dist", but inside
# the container we place it at /app/dashboard/dist so the relative path still
# resolves correctly from /app/backend → /app/dashboard/dist.
COPY --from=frontend-builder /build/dashboard/dist /app/dashboard/dist

# Persistent volume mount point for the SQLite database
RUN mkdir -p /data && chown appuser:appgroup /data

# Switch to non-root
USER appuser

# Expose the API port
EXPOSE 8000

# Liveness probe — Docker will mark the container unhealthy if this fails
HEALTHCHECK --interval=30s --timeout=10s --start-period=20s --retries=3 \
    CMD curl -f http://localhost:8000/api/health || exit 1

# Production entry point:
#   - 2 Uvicorn workers (safe default for single-CPU container; tune with WORKERS env var)
#   - Bind to all interfaces so Docker port mapping works
CMD uvicorn main:app \
      --host 0.0.0.0 \
      --port 8000 \
      --workers ${WORKERS:-2} \
      --log-level ${LOG_LEVEL:-info} \
      --no-access-log
