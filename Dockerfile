# =============================================================================
# Forza Horizon 6 Telemetry - Production Dockerfile
#
# Optimized multi-stage build for the collector (primary daemon).
# Use with: docker compose build
#
# For the dashboard (TUI), see Dockerfile.dashboard
# =============================================================================

# syntax=docker/dockerfile:1
FROM python:3.12-slim AS builder

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /build

# Build-time system dependencies (only what we need for wheels)
RUN apt-get update && apt-get install -y --no-install-recommends \
        build-essential \
        gcc \
    && rm -rf /var/lib/apt/lists/*

# Install Python dependencies into a virtual environment (cleaner than --user)
RUN python -m venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# -----------------------------------------------------------------------------
# Runtime image (minimal)
# -----------------------------------------------------------------------------
FROM python:3.12-slim AS runtime

# OCI labels (good for production tooling)
LABEL org.opencontainers.image.title="Forza Horizon 6 Telemetry Collector" \
      org.opencontainers.image.description="High-quality UDP telemetry collector for Forza Horizon 6 with MongoDB persistence" \
      org.opencontainers.image.source="https://github.com/yourname/forza-telemetry-fh6" \
      org.opencontainers.image.licenses="MIT"

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PATH="/opt/venv/bin:$PATH" \
    # Sensible production defaults (override via compose or -e)
    FORZA_UDP_HOST=0.0.0.0 \
    FORZA_UDP_PORT=20066 \
    MONGO_URI=mongodb://mongo:27017 \
    MONGO_DB=forza_telemetry_fh6 \
    SOCKET_PATH=/tmp/forza-telemetry.sock \
    LOG_LEVEL=INFO \
    # Disable rich color if running in non-tty (docker logs)
    FORCE_COLOR=0

# Create non-root user early
RUN groupadd --system --gid 1000 forza \
 && useradd --system --uid 1000 --gid forza --home /app --shell /sbin/nologin forza

# Install only runtime OS packages (mongosh not needed in app container)
RUN apt-get update && apt-get install -y --no-install-recommends \
        ca-certificates \
        tini \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Bring in the virtualenv from builder
COPY --from=builder /opt/venv /opt/venv

# Copy only what we need (respect .dockerignore)
COPY --chown=forza:forza . .

# Install the package in editable mode (lightweight)
RUN pip install --no-deps -e .

# Prepare runtime directories with correct ownership
RUN mkdir -p /tmp /app/logs /app/.pids \
 && chown -R forza:forza /app /tmp

# Use non-root
USER forza
WORKDIR /app

# tini is an excellent init for Python async apps (proper signal forwarding)
ENTRYPOINT ["/usr/bin/tini", "--"]

# Default to collector (the production daemon)
CMD ["forza-telemetry", "collector"]

# Expose telemetry port (documentation only)
EXPOSE 20066/udp
