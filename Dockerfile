# Single-container image for Hugging Face Spaces / Render.
# Builds React frontend, serves it from FastAPI on one origin.

# --- Frontend build ---
FROM node:22-alpine AS frontend
WORKDIR /fe
COPY frontend/package.json frontend/package-lock.json* ./
RUN npm ci
COPY frontend/ ./
RUN npm run build

# --- Backend + static frontend ---
FROM python:3.12-slim
ENV PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    DEMO_SCALE=small

# PostgreSQL runs inside the container (localhost only) to back the
# "Connect PostgreSQL" demo preset — no external demo server needed.
RUN apt-get update \
    && apt-get install -y --no-install-recommends postgresql \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY backend/requirements.txt .
RUN pip install --upgrade pip && pip install -r requirements.txt

COPY backend/app ./app
COPY backend/tests/seed_postgres.sql ./tests/seed_postgres.sql
COPY backend/data/docs ./data/docs
COPY --from=frontend /fe/dist ./static
COPY entrypoint.sh ./entrypoint.sh
RUN chmod +x entrypoint.sh

VOLUME ["/app/data"]
EXPOSE 7860

ENTRYPOINT ["./entrypoint.sh"]
CMD uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-7860}
