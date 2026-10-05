# Creativo

Generation platform control plane. The browser talks only to the API. A single orchestrator consumes the queue and pushes jobs to workers. The fixture worker proves the loop without a GPU.

## Run

```powershell
.\scripts\bootstrap.ps1
python -m uv run --package creativo-api python -m creativo_api
python -m uv run --package creativo-orchestrator python -m creativo_orchestrator
python -m uv run --package creativo-worker python -m creativo_worker
$env:WORKER_ID = "worker-flux-1"
$env:WORKER_PORT = "8110"
$env:WORKER_ADVERTISE_URL = "http://localhost:8110"
$env:WORKER_MODEL_ID = "flux"
python -m uv run --package creativo-flux python -m creativo_flux
npm --prefix apps/web run dev
```

Open http://localhost:3000 and use the development entrance. The first sign-in receives 40 credits.

- API: http://localhost:8000
- Orchestrator health: http://localhost:8090/health
- Fixture worker: http://localhost:8100/health
- Flux worker: http://localhost:8110/health

Flux is FLUX.1 schnell (Apache-2.0). The transformer is the Q4_K_S GGUF, and the text encoders come from the official Black Forest Labs repository. On a 6GB GPU the worker streams layers through the card, so a 512 image is the practical size and requests run one at a time. On a GPU that can keep the model loaded, the orchestrator packs up to five waiting requests — different prompts — into one pass. The first start downloads the weights into `data/hf`.

Postgres and Redis come from Docker Compose. Postgres is published on host port 5433 so it can run beside a local PostgreSQL. Copy `.env.example` to `.env` before starting; the bootstrap script does that.

## GPU host

CI publishes four images to the GitHub registry: `control`, `web`, `flux`, and `pod`. The pipeline builds them and, for a Docker host, copies `deploy/compose.yaml` over SSH and runs Compose. It never calls a GPU vendor. The public address is `WEB_PUBLIC_URL` and `API_PUBLIC_URL`. A RunPod proxy URL and a later domain are the same two settings.

A RunPod pod is one container, so it runs `ghcr.io/<owner>/creativo/pod:latest`. That image starts Postgres, Redis, the API, the orchestrator, the studio, and the workers named in `CREATIVO_WORKERS` (default `flux`). In the pod form, set that image, publish port 3000, and paste `deploy/pod.env.example`. Point `WEB_PUBLIC_URL`, `API_PUBLIC_URL`, and `CORS_ORIGINS` at the proxy URL for port 3000. Put `HF_TOKEN` there too. Attach a volume; when `/workspace` exists, weights and pictures are stored under `/workspace/creativo` and survive a restart. GitHub packages are private until you make `pod` public or give the pod registry credentials. After CI publishes a new tag, restart the pod on that tag.

Another model is another worker program in the same image, added to `CREATIVO_WORKERS`. The programs that exist today are `flux` and `fixture`.

A machine that has Docker and the NVIDIA container toolkit uses the split images instead. From a checkout:

```bash
cp deploy/env.example deploy/.env
# Set the public URLs, the secrets, HF_TOKEN, and CREATIVO_IMAGE_PREFIX=ghcr.io/<owner>/creativo
docker compose -f deploy/compose.yaml --profile flux up -d
```

The deploy action is this same command over SSH. Secrets are `DEPLOY_HOST`, `DEPLOY_USER`, and `DEPLOY_SSH_KEY`. `DEPLOY_PORT` is 22 unless the machine uses another port. The host keeps its own `.env`; the action does not overwrite it. Leave this host and the images stay the ones CI already built.

## What this loop guarantees

- Explicit model selection. `model=auto` is rejected.
- Safety runs before a credit reservation, and a blocked prompt is never enhanced.
- Credits are reserved, then consumed or refunded once. Worker GPU time is not the bill.
- `Idempotency-Key` is scoped to the user.
- Cancel is honored while a job is still queued, and the reservation is released.
- A failed or exhausted job is refunded. A retryable fixture failure runs a second attempt.
- Another user cannot read your generation.
- Workers authenticate every request and never see the billing database.

Qwen Image, Wan, and MiniMax H3 are in the catalog and disabled. Enabling one requires a pinned checkpoint, a license check, an adapter, and a worker program in the same image.

## Tests

```powershell
python -m uv run pytest
```
