FROM node:22-bookworm-slim AS web

WORKDIR /app
COPY apps/web/package.json apps/web/package-lock.json ./
RUN npm ci
COPY apps/web ./
ARG NEXT_PUBLIC_API_URL=
ENV NEXT_PUBLIC_API_URL=$NEXT_PUBLIC_API_URL \
    API_PROXY_URL=http://127.0.0.1:8000
RUN npm run build

FROM python:3.12-slim-bookworm

COPY --from=ghcr.io/astral-sh/uv:0.12.23 /uv /uvx /bin/
RUN printf '#!/bin/sh\nexit 101\n' > /usr/sbin/policy-rc.d \
    && chmod +x /usr/sbin/policy-rc.d \
    && apt-get update \
    && apt-get install -y --no-install-recommends \
        libatomic1 \
        libgomp1 \
        libstdc++6 \
        postgresql \
        postgresql-client \
        redis-server \
    && rm -rf /var/lib/apt/lists/*

COPY --from=web /usr/local/bin/node /usr/local/bin/node
COPY --from=web /app /opt/web

WORKDIR /app
COPY pyproject.toml uv.lock ./
COPY packages packages
COPY apps apps
COPY workers workers
RUN uv sync --frozen --no-dev \
    --package creativo-api \
    --package creativo-orchestrator \
    --package creativo-worker \
    --package creativo-flux

COPY deploy/pod/entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh

ENV PATH="/app/.venv/bin:$PATH" \
    PYTHONUNBUFFERED=1 \
    CREATIVO_WORKERS=flux

EXPOSE 3000
ENTRYPOINT ["/entrypoint.sh"]
