# Multi-stage build: frontend then backend
FROM node:20-alpine AS frontend-builder

WORKDIR /app/frontend

# Install bun
RUN npm install -g bun

# Copy frontend files
COPY frontend/package.json frontend/bun.lockb ./
RUN bun install

COPY frontend/ ./
COPY config/ ../config/

# Build frontend (outputs to ../templates/)
RUN bun run build


# Backend runtime image
FROM python:3.11-slim

WORKDIR /app

# Install system dependencies
RUN apt-get update && apt-get install -y \
    ripgrep \
    fd-find \
    git \
    emacs-nox \
    curl \
    && rm -rf /var/lib/apt/lists/* \
    && ln -s $(which fdfind) /usr/local/bin/fd

# Install uv for dependency management
COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv

# Set uv to use system Python
ENV UV_SYSTEM_PYTHON=1

# Install Python dependencies first (for better layer caching)
COPY pyproject.toml ./
RUN uv pip compile pyproject.toml -o requirements.txt
RUN uv pip install --no-cache -r requirements.txt

# Copy Python application
COPY pkm_bridge/ ./pkm_bridge/
COPY config/ ./config/
COPY scripts/ ./scripts/
COPY pkm-bridge-server.py ./
COPY migrate_add_cost_tracking.py ./
COPY docker-entrypoint.sh ./

# Copy built frontend from first stage
COPY --from=frontend-builder /app/templates ./templates/

# Create non-root user
RUN useradd -m -u 1000 pkm && \
    chown -R pkm:pkm /app

# Expose port
EXPOSE 8000

# Health check
HEALTHCHECK --interval=30s --timeout=10s --start-period=5s --retries=3 \
    CMD curl -f http://localhost:8000/health || exit 1

# Run entrypoint script (runs migrations, then starts server)
CMD ["./docker-entrypoint.sh"]
