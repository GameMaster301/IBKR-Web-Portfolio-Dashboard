# ── Stage 1: dependency builder ───────────────────────────────────────────────
FROM python:3.12-slim AS builder

WORKDIR /build

# System deps needed to compile some wheels (e.g. reportlab, numpy)
RUN apt-get update && apt-get install -y --no-install-recommends \
        gcc g++ libffi-dev && \
    rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir --prefix=/install -r requirements.txt


# ── Stage 2: runtime image ────────────────────────────────────────────────────
FROM python:3.12-slim

# Non-root user for security
RUN groupadd -r appuser && useradd -r -g appuser appuser

WORKDIR /app

# Copy installed packages from builder
COPY --from=builder /install /usr/local

# Copy application source
COPY --chown=appuser:appuser . .

USER appuser

# ── Environment defaults (all overridable at runtime) ─────────────────────────
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    # Dashboard listens on all interfaces so Docker port mapping works
    DASH_HOST=0.0.0.0 \
    DASH_PORT=8050 \
    # No browser in a container
    OPEN_BROWSER=0

EXPOSE 8050

# Lightweight healthcheck: hit the Dash root every 30 s
HEALTHCHECK --interval=30s --timeout=10s --start-period=20s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8050/')" \
    || exit 1

CMD ["python", "main.py"]
