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

WORKDIR /app

COPY backend/requirements.txt .
RUN pip install --upgrade pip && pip install -r requirements.txt

COPY backend/app ./app
COPY backend/data/docs ./data/docs
COPY --from=frontend /fe/dist ./static

VOLUME ["/app/data"]
EXPOSE 7860

CMD uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-7860}
