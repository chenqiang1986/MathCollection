FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PORT=8080

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
        build-essential \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

COPY src ./webapp/src

RUN mkdir -p /app/data

EXPOSE 8080

# Cloud Run injects $PORT. Bind gunicorn to it. One worker because the app
# uses a per-request SQLite file on a GCS-Fuse-mounted volume.
CMD exec gunicorn \
    --chdir webapp/src \
    --bind "0.0.0.0:${PORT}" \
    --workers 1 \
    --threads 8 \
    --timeout 120 \
    "app:app"
