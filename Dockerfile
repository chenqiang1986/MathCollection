# Build from the repo root so `common/`, `worker/`, and `webapp/` are all
# in the build context:
#   docker build -t mathcollection-web .
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PORT=8000 \
    PYTHONPATH=/app

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
        build-essential \
    && rm -rf /var/lib/apt/lists/*

COPY webapp/requirements.txt ./webapp/requirements.txt
RUN pip install --no-cache-dir -r webapp/requirements.txt

# Shared code (storage, db_setup, figures, agent_util, prompts) lives in
# `common/`; the Flask app and refine agent live in `webapp/`. Both go on
# PYTHONPATH=/app so absolute imports like `from common import ...` and
# `from webapp.src.lib import ...` resolve.
COPY common ./common
COPY webapp ./webapp

RUN mkdir -p /app/data

EXPOSE 8000

# Entrypoint hydrates secrets from Secret Manager when GCP_PROJECT_ID is set
# (no-op on Cloud Run / local), then execs gunicorn. See webapp/entrypoint.sh.
CMD ["/app/webapp/entrypoint.sh"]
