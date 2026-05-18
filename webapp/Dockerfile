# Build from the repo root so both `common/` and `webapp/` are in the
# build context:
#   docker build -f webapp/Dockerfile -t mathcollection-web .
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PORT=8080 \
    PYTHONPATH=/app

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
        build-essential \
    && rm -rf /var/lib/apt/lists/*

COPY webapp/requirements.txt ./webapp/requirements.txt
RUN pip install --no-cache-dir -r webapp/requirements.txt

# Shared code (storage, db_setup, figures, agent_util, prompts) lives in
# `common/`; the Flask app and refine agent live in `webapp/src/`. Both
# go on PYTHONPATH (`/app` and `/app/webapp/src` are visible via the env
# var below and gunicorn's --chdir).
COPY common ./common
COPY webapp/src ./webapp/src

RUN mkdir -p /app/data

EXPOSE 8080

# Cloud Run injects $PORT. Bind gunicorn to it. One worker because the app
# uses a per-request SQLite file on a GCS-Fuse-mounted volume.
# PYTHONPATH=/app lets `from common import ...` resolve; --chdir makes
# `app:app` resolve from `webapp/src/app.py`.
CMD exec gunicorn \
    --chdir webapp/src \
    --bind "0.0.0.0:${PORT}" \
    --workers 1 \
    --threads 8 \
    --timeout 120 \
    "app:app"
