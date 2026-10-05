FROM python:3.12-slim-bookworm

COPY --from=ghcr.io/astral-sh/uv:0.12.23 /uv /uvx /bin/
WORKDIR /app

COPY pyproject.toml uv.lock ./
COPY packages packages
COPY apps apps
COPY workers workers

RUN uv sync --frozen --no-dev \
    --package creativo-api \
    --package creativo-orchestrator \
    --package creativo-worker

ENV PATH="/app/.venv/bin:$PATH" \
    PYTHONUNBUFFERED=1 \
    STORAGE_DIR=/data/objects

CMD ["python", "-m", "creativo_api"]
