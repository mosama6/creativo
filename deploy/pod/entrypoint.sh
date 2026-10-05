#!/bin/bash
# One container: database, queue, control plane, studio, and whichever workers
# CREATIVO_WORKERS names. A RunPod pod is this process. A later host can run the
# same programs from deploy/compose.yaml instead.
set -euo pipefail
set -m

if [ -d /workspace ]; then
  default_root=/workspace/creativo
else
  default_root=/data/creativo
fi
DATA_ROOT="${CREATIVO_DATA_ROOT:-$default_root}"
PGDATA="$DATA_ROOT/pg"
PG_BIN="$(echo /usr/lib/postgresql/*/bin)"

mkdir -p "$DATA_ROOT/objects" "$DATA_ROOT/hf/hub" "$DATA_ROOT/redis" "$PGDATA"
chown -R postgres:postgres "$PGDATA" "$DATA_ROOT/redis"

export DATABASE_URL="postgresql+asyncpg://creativo@127.0.0.1:5432/creativo"
export REDIS_URL="redis://127.0.0.1:6379/0"
export STORAGE_DIR="$DATA_ROOT/objects"
export HF_HUB_CACHE="$DATA_ROOT/hf/hub"
export HF_HUB_DISABLE_SYMLINKS_WARNING=1
export PATH="/app/.venv/bin:$PATH"
export RELEASE_WEIGHTS_AFTER_JOB=true

if [ ! -s "$PGDATA/PG_VERSION" ]; then
  su postgres -c "$PG_BIN/initdb -D '$PGDATA' --username=postgres --auth=trust"
fi

su postgres -c "$PG_BIN/pg_ctl -D '$PGDATA' -w -o '-c listen_addresses=127.0.0.1' start"
su postgres -c "$PG_BIN/psql -d postgres -v ON_ERROR_STOP=1" <<'SQL'
DO $$
BEGIN
  IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'creativo') THEN
    CREATE ROLE creativo LOGIN SUPERUSER;
  END IF;
END
$$;
SQL
if ! su postgres -c "$PG_BIN/psql -d postgres -tAc \"SELECT 1 FROM pg_database WHERE datname = 'creativo'\"" | grep -q 1; then
  su postgres -c "$PG_BIN/psql -d postgres -c 'CREATE DATABASE creativo OWNER creativo'"
fi

redis-server --bind 127.0.0.1 --port 6379 --dir "$DATA_ROOT/redis" --daemonize yes
python -c "from creativo_db.migrate import upgrade; upgrade()"

python -m creativo_api &
python -m creativo_orchestrator &

for name in ${CREATIVO_WORKERS:-flux}; do
  case "$name" in
    flux)
      WORKER_ID=worker-flux-1 \
      WORKER_MODEL_ID=flux \
      WORKER_PORT=8110 \
      WORKER_ADVERTISE_URL=http://127.0.0.1:8110 \
      python -m creativo_flux &
      ;;
    qwen-image)
      WORKER_ID=worker-qwen-1 \
      WORKER_MODEL_ID=qwen-image \
      WORKER_PORT=8120 \
      WORKER_ADVERTISE_URL=http://127.0.0.1:8120 \
      python -m creativo_qwen &
      ;;
    fixture)
      WORKER_ID=worker-fixture-1 \
      WORKER_MODEL_ID=fixture-image \
      WORKER_PORT=8100 \
      WORKER_ADVERTISE_URL=http://127.0.0.1:8100 \
      python -m creativo_worker &
      ;;
    *)
      echo "CREATIVO_WORKERS has no program named $name" >&2
      exit 1
      ;;
  esac
done

(
  cd /opt/web
  HOSTNAME=0.0.0.0 PORT=3000 API_PROXY_URL=http://127.0.0.1:8000 \
    node node_modules/next/dist/bin/next start -H 0.0.0.0 -p 3000
) &

trap 'kill $(jobs -p) 2>/dev/null || true; su postgres -c "$PG_BIN/pg_ctl -D \"$PGDATA\" stop" || true' TERM INT
wait -n
exit 1
