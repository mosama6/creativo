import asyncio
import logging
import time
from contextlib import asynccontextmanager
from dataclasses import dataclass, field

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from redis.asyncio import Redis

from creativo_common.gpu_lock import gpu_lock
from creativo_common.keys import worker_key, workers_model_key
from creativo_common.logging import configure_logging
from creativo_common.settings import PlatformSettings, get_settings
from creativo_common.storage import FileStorage
from creativo_common.worker_auth import WorkerAuthError, verify
from creativo_contracts.worker import (
    GenerateRequest,
    WorkerHeartbeat,
    WorkerJobView,
    WorkerOutput,
    WorkerUsage,
)
from creativo_qwen.engine import QwenEngine

logger = logging.getLogger(__name__)


class WorkerAuthFailure(Exception):
    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


@dataclass
class JobState:
    attempt: int
    token: object
    status: str = "processing"
    retryable: bool = False
    error_code: str | None = None
    error_message: str | None = None
    output: WorkerOutput | None = None
    outputs: list[WorkerOutput] = field(default_factory=list)
    gpu_seconds: float = 0


def create_app() -> FastAPI:
    configure_logging()
    settings = get_settings()
    settings.assert_production_safe()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        app.state.settings = settings
        app.state.storage = FileStorage(settings.storage_dir)
        app.state.redis = Redis.from_url(settings.redis_url, decode_responses=True)
        app.state.jobs = {}
        app.state.engine = QwenEngine()
        app.state.ready = False
        app.state.stopped = False
        app.state.load_error = None
        task = asyncio.create_task(heartbeat_loop(app))
        try:
            await asyncio.to_thread(app.state.engine.probe)
            app.state.ready = True
        except Exception as exc:
            app.state.load_error = f"{type(exc).__name__}: {exc}"
            logger.exception("qwen probe failed")
        await publish_heartbeat(app)
        yield
        app.state.ready = False
        app.state.stopped = True
        task.cancel()
        await app.state.redis.delete(worker_key(settings.worker_id))
        await app.state.redis.aclose()

    app = FastAPI(title="Creativo Qwen worker", lifespan=lifespan)

    @app.exception_handler(WorkerAuthFailure)
    async def auth_error(_: Request, exc: WorkerAuthFailure) -> JSONResponse:
        return JSONResponse({"error": {"code": exc.code}}, status_code=401)

    @app.get("/health")
    async def health() -> dict[str, str]:
        if app.state.load_error:
            return {"status": "error", "detail": app.state.load_error}
        if app.state.ready:
            return {"status": "ok", "model": "qwen-image"}
        return {"status": "starting", "model": "qwen-image"}

    @app.post("/generate", status_code=202)
    async def generate(request: Request) -> JSONResponse:
        body = await request.body()
        authorize(request, body)
        payload = GenerateRequest.model_validate_json(body)
        if payload.job_id != request.headers.get("x-creativo-job-id"):
            raise WorkerAuthFailure("bad_signature")
        if payload.model != settings.worker_model_id:
            return JSONResponse(
                {"job_id": payload.job_id, "status": "failed", "error_code": "unsupported_mode"},
                status_code=422,
            )
        if not app.state.ready:
            return JSONResponse({"error": {"code": "worker_busy"}}, status_code=429)
        jobs: dict[str, JobState] = app.state.jobs
        current = jobs.get(payload.job_id)
        if current is not None and current.attempt == payload.attempt:
            return JSONResponse({"job_id": payload.job_id, "status": "accepted"}, status_code=202)
        if any(job.status == "processing" and job_id != payload.job_id for job_id, job in jobs.items()):
            return JSONResponse({"error": {"code": "worker_busy"}}, status_code=429)
        token = object()
        jobs[payload.job_id] = JobState(attempt=payload.attempt, token=token)
        asyncio.create_task(run_job(app, payload, token))
        return JSONResponse({"job_id": payload.job_id, "status": "accepted"}, status_code=202)

    @app.get("/jobs/{job_id}")
    async def read_job(job_id: str, request: Request) -> JSONResponse:
        authorize(request, await request.body())
        if request.headers.get("x-creativo-job-id") != job_id:
            raise WorkerAuthFailure("bad_signature")
        state: JobState | None = app.state.jobs.get(job_id)
        if state is None:
            return JSONResponse({"error": {"code": "job_not_found"}}, status_code=404)
        view = WorkerJobView(
            job_id=job_id,
            status=state.status,
            retryable=state.retryable,
            error_code=state.error_code,
            error_message=state.error_message,
            output=state.output,
            outputs=state.outputs,
            usage=WorkerUsage(gpu_seconds=state.gpu_seconds) if state.status == "completed" else None,
        )
        return JSONResponse(view.model_dump(mode="json"))

    return app


def authorize(request: Request, body: bytes) -> None:
    settings: PlatformSettings = request.app.state.settings
    try:
        verify(
            settings.worker_shared_secret,
            timestamp=request.headers.get("x-creativo-timestamp"),
            job_id=request.headers.get("x-creativo-job-id"),
            signature=request.headers.get("x-creativo-signature"),
            body=body,
        )
    except WorkerAuthError as exc:
        raise WorkerAuthFailure(exc.code) from exc


async def publish_heartbeat(app: FastAPI) -> None:
    settings: PlatformSettings = app.state.settings
    redis: Redis = app.state.redis
    engine: QwenEngine = app.state.engine
    jobs: dict[str, JobState] = app.state.jobs
    active = sum(1 for job in jobs.values() if job.status == "processing")
    if not app.state.ready:
        status = "STARTING"
    elif active >= 1:
        status = "BUSY"
    else:
        status = "READY"
    beat = WorkerHeartbeat(
        worker_id=settings.worker_id,
        model=settings.worker_model_id,
        model_version="v1",
        gpu=engine.gpu_name,
        vram_gb=engine.vram_gb,
        status=status,  # type: ignore[arg-type]
        active_jobs=active,
        max_concurrent_jobs=1,
        max_batch_size=1,
        queue_depth=active,
        advertise_url=settings.worker_advertise_url,
        hourly_micro_usd=0,
        last_error=app.state.load_error,
    )
    await redis.set(worker_key(settings.worker_id), beat.model_dump_json(), ex=15)
    await redis.sadd(workers_model_key(settings.worker_model_id), settings.worker_id)


async def heartbeat_loop(app: FastAPI) -> None:
    while not app.state.stopped:
        try:
            await publish_heartbeat(app)
        except Exception:
            logger.exception("heartbeat failed")
        await asyncio.sleep(2)


async def run_job(app: FastAPI, payload: GenerateRequest, token: object) -> None:
    started = time.perf_counter()
    release = app.state.settings.release_weights_after_job
    width = int(payload.parameters.get("width") or 1024)
    height = int(payload.parameters.get("height") or 1024)
    steps = int(payload.parameters.get("num_inference_steps") or 50)
    true_cfg = float(payload.parameters.get("true_cfg_scale") or 4.0)
    try:
        with gpu_lock():
            try:
                if release or app.state.engine.pipe is None:
                    await asyncio.to_thread(app.state.engine.load)
                data = await asyncio.to_thread(
                    app.state.engine.generate, payload.prompt, width, height, steps, true_cfg
                )
            finally:
                if release:
                    await asyncio.to_thread(app.state.engine.unload)
        if app.state.jobs.get(payload.job_id) is None or app.state.jobs[payload.job_id].token is not token:
            return
        key = f"outputs/{payload.generation_id}/attempt-{payload.attempt}.png"
        app.state.storage.put(key, data)
        output = WorkerOutput(
            type="image", storage_key=key, content_type="image/png", byte_size=len(data)
        )
        state = app.state.jobs[payload.job_id]
        state.status = "completed"
        state.gpu_seconds = round(time.perf_counter() - started, 3)
        state.output = output
        state.outputs = [output]
    except Exception as exc:
        logger.exception("qwen job failed")
        state = app.state.jobs.get(payload.job_id)
        if state is None or state.token is not token or state.status != "processing":
            return
        oom = exc.__class__.__name__ == "OutOfMemoryError"
        state.status = "failed"
        state.retryable = not oom
        state.error_code = "cuda_oom" if oom else "model_crash"
        state.error_message = (
            "The GPU ran out of memory." if oom else "Qwen failed while generating."
        )
    finally:
        await publish_heartbeat(app)


def app() -> FastAPI:
    return create_app()
