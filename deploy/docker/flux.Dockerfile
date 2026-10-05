FROM python:3.12-slim-bookworm

COPY --from=ghcr.io/astral-sh/uv:0.12.23 /uv /uvx /bin/
WORKDIR /app

RUN apt-get update \
    && apt-get install -y --no-install-recommends libgomp1 \
    && rm -rf /var/lib/apt/lists/*

COPY pyproject.toml uv.lock ./
COPY packages packages
COPY apps apps
COPY workers workers

RUN uv sync --frozen --no-dev --package creativo-flux

ENV PATH="/app/.venv/bin:$PATH" \
    PYTHONUNBUFFERED=1 \
    STORAGE_DIR=/data/objects \
    HF_HUB_CACHE=/data/hf/hub \
    HF_HUB_DISABLE_SYMLINKS_WARNING=1

CMD ["python", "-m", "creativo_flux"]
