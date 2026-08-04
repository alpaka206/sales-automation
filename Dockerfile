# The console is a React app compiled ahead of time; FastAPI only serves the result.
#
# This stage exists because the build output is deliberately NOT in git — a committed
# artifact goes stale the first time someone forgets to rebuild. Without it, an image
# built from a clean checkout has no bundle and every /app URL answers 503.
#
# package.json first, sources second: the dependency layer is then reused by every build
# that only changed a screen.
FROM node:24-alpine AS console

WORKDIR /build/frontend
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci
COPY frontend/ ./
# vite.config.ts writes ../src/api/static/app — the path FastAPI serves the console from.
RUN npm run build


FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

COPY . .
# After COPY . ., so the build wins over whatever a developer's working tree had; before
# pip install, so package-data picks the bundle up.
COPY --from=console /build/src/api/static/app ./src/api/static/app
# Fail here rather than at the first request. A missing bundle is not a crash — it is a
# 503 on every /app URL, and nothing says so until someone opens the console.
RUN test -f src/api/static/app/index.html

RUN python -m pip install --no-cache-dir --upgrade pip setuptools \
    && python -m pip install --no-cache-dir ".[postgres]" \
    && addgroup --system app \
    && adduser --system --ingroup app app \
    && mkdir -p data logs \
    && chown -R app:app data logs

USER app

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=3s --start-period=20s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/healthz', timeout=2)"

CMD ["uvicorn", "src.api.main:app", "--host", "0.0.0.0", "--port", "8000"]
