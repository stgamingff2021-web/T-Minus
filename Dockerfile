# ---------- 1. build the React dashboard ----------
FROM node:22-alpine AS frontend
WORKDIR /fe
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci --no-audit --no-fund
COPY frontend/ ./
RUN npm run build

# ---------- 2. backend + built frontend in one image ----------
FROM python:3.12-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1 \
    FASTEMBED_CACHE_PATH=/opt/fastembed
# pango/harfbuzz + Noto fonts (incl. Tamil) for the PDF briefing; tesseract for scanned plans
RUN apt-get update && apt-get install -y --no-install-recommends \
        libpango-1.0-0 libpangoft2-1.0-0 libharfbuzz0b libharfbuzz-subset0 fonts-noto-core fonts-dejavu-core \
        tesseract-ocr curl \
    && rm -rf /var/lib/apt/lists/*
WORKDIR /app
COPY backend/requirements.txt backend/requirements.txt
RUN pip install -r backend/requirements.txt
# Pre-fetch the local embedding model so the running app needs no download (falls back to TF-IDF if this fails).
RUN python -c "from fastembed import TextEmbedding; list(TextEmbedding('BAAI/bge-small-en-v1.5').embed(['ok']))" \
    || echo "fastembed model prefetch failed — TF-IDF fallback will be used at runtime"
COPY backend/ backend/
COPY config/ config/
COPY --from=frontend /fe/dist frontend/dist
RUN useradd -m -u 1000 tminus && mkdir -p /data && chown -R tminus /data /app
USER tminus
ENV CONFIG_DIR=/app/config FRONTEND_DIST=/app/frontend/dist DATA_DIR=/data \
    DATABASE_URL=sqlite:////data/tminus.db
VOLUME ["/data"]
WORKDIR /app/backend
EXPOSE 8000
HEALTHCHECK --interval=60s --timeout=10s --start-period=40s CMD curl -fsS http://127.0.0.1:${PORT:-8000}/api/health || exit 1
CMD ["sh", "-c", "uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8000} --proxy-headers --forwarded-allow-ips='*'"]
