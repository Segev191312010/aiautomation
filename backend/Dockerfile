# =============================================================================
# Trading Platform — Backend Dockerfile
# 
# Multi-layer production-ready image:
# - Python 3.12 slim base
# - Non-root user for security
# - Health checks
# - Environment-based tuning
# =============================================================================

FROM python:3.12-slim

# Create non-root user for least-privilege execution
RUN groupadd --gid 1001 appgroup && \
    useradd --uid 1001 --gid appgroup --shell /bin/bash --create-home appuser

# Install runtime dependencies (curl for healthcheck, any system libs needed)
RUN apt-get update && \
    apt-get install -y --no-install-recommends \
    curl \
    ca-certificates && \
    rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Install Python dependencies (separate layer for better caching)
# Only rebuilds when requirements.txt changes
COPY backend/requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

# Copy backend application code
COPY backend/ ./

# Create persistent volume mount point for SQLite database
RUN mkdir -p /data && chown appuser:appgroup /data

# Switch to non-root user before running container
USER appuser

# Expose API port
EXPOSE 8000

# Health check — Docker marks unhealthy if this fails
# Checks that /api/health endpoint responds with 200
HEALTHCHECK --interval=30s --timeout=10s --start-period=20s --retries=3 \
    CMD curl -f http://localhost:8000/api/health || exit 1

# Production entry point:
# - Uvicorn with configurable worker count (default 2)
# - Bind to 0.0.0.0 so Docker port mapping works
# - JSON logs to stdout (captured by docker logs / docker compose logs)
CMD uvicorn main:app \
      --host 0.0.0.0 \
      --port 8000 \
      --workers ${WORKERS:-2} \
      --log-level ${LOG_LEVEL:-info} \
      --no-access-log
