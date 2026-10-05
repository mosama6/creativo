import asyncio
import logging
import time
from contextlib import asynccontextmanager
from dataclasses import dataclass, field

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from redis.asyncio import Redis

from creativo_common.keys import worker_key, workers_model_key
from creativo_common.logging import configure_logging
from creativo_common.settings import PlatformSettings, get_settings
from creativo_common.storage import FileStorage, StorageError
from creativo_common.worker_auth import WorkerAuthError, verify
from creativo_contracts.worker import (
    GenerateBatchRequest,
    GenerateRequest,
    WorkerHeartbeat,
    WorkerJobView,
    WorkerOutput,
    WorkerUsage,
)
from creativo_flux.engine import FluxEngine

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
        app.state.engine = FluxEngine()
        app.state.ready = False
        app.state.stopped = False
        app.state.load_error = None
        task = asyncio.create_task(heartbeat_loop(app))
        try:
            await asyncio.to_thread(app.state.engine.probe)
            await publish_heartbeat(app)
            await asyncio.to_thread(app.state.engine.load)
            app.state.ready = True
        except Exception as exc:
            app.state.load_error = f"{type(exc).__name__}: {exc}"
            logger.exception("flux weights failed to load")
        await publish_heartbeat(app)
        yield
        app.state.ready = False
        app.state.stopped = True
        task.cancel()
        await app.state.redis.delete(worker_key(settings.worker_id))
        await app.state.redis.aclose()

    app = FastAPI(title="Creativo Flux worker", lifespan=lifespan)

    @app.exception_handler(WorkerAuthFailure)
    async def auth_error(_: Request, exc: WorkerAuthFailure) -> JSONResponse:
        return JSONResponse({"error": {"code": exc.code}}, status_code=401)

    @app.get("/health")
    async def health() -> dict[str, str]:
        if app.state.ready:
            return {"status": "ok", "model": "flux"}
        if app.state.load_error:
            return {"status": "error", "detail": app.state.load_error}
        return {"status": "starting", "model": "flux"}

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
        busy_elsewhere = any(
            job.status == "processing" and job_id != payload.job_id for job_id, job in jobs.items()
        )
        if busy_elsewhere:
            return JSONResponse({"error": {"code": "worker_busy"}}, status_code=429)
        token = object()
        jobs[payload.job_id] = JobState(attempt=payload.attempt, token=token)
        asyncio.create_task(run_job(app, payload, token))
        return JSONResponse({"job_id": payload.job_id, "status": "accepted"}, status_code=202)

    @app.post("/generate-batch", status_code=202)
    async def generate_batch(request: Request) -> JSONResponse:
        body = await request.body()
        authorize(request, body)
        batch = GenerateBatchRequest.model_validate_json(body)
        lead = request.headers.get("x-creativo-job-id")
        if lead != batch.jobs[0].job_id:
            raise WorkerAuthFailure("bad_signature")
        if any(job.model != settings.worker_model_id for job in batch.jobs):
            return JSONResponse(
                {"job_id": lead, "status": "failed", "error_code": "unsupported_mode"},
                status_code=422,
            )
        if not app.state.ready:
            return JSONResponse({"error": {"code": "worker_busy"}}, status_code=429)
        accepted = _accept_many(app, batch.jobs)
        if isinstance(accepted, JSONResponse):
            return accepted
        asyncio.create_task(run_batch(app, batch.jobs, accepted))
        return JSONResponse({"job_id": lead, "status": "accepted"}, status_code=202)

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
            usage=WorkerUsage(gpu_seconds=state.gpu_seconds)
            if state.status == "completed"
            else None,
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
    engine: FluxEngine = app.state.engine
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
        max_batch_size=engine.max_batch_size,
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


def _accept_many(app: FastAPI, payloads: list[GenerateRequest]) -> JSONResponse | object:
    jobs: dict[str, JobState] = app.state.jobs
    incoming = {payload.job_id for payload in payloads}
    if all(
        (current := jobs.get(payload.job_id)) is not None and current.attempt == payload.attempt
        for payload in payloads
    ):
        return JSONResponse({"job_id": payloads[0].job_id, "status": "accepted"}, status_code=202)
    busy = any(job.status == "processing" and job_id not in incoming for job_id, job in jobs.items())
    if busy:
        return JSONResponse({"error": {"code": "worker_busy"}}, status_code=429)
    token = object()
    for payload in payloads:
        jobs[payload.job_id] = JobState(attempt=payload.attempt, token=token)
    return token


def _reference(app: FastAPI, payload: GenerateRequest) -> bytes | None:
    if not payload.inputs.images:
        return None
    return app.state.storage.get(payload.inputs.images[0].storage_key)


def _shape(payload: GenerateRequest) -> tuple[int, int, int, float, int, float]:
    return (
        int(payload.parameters.get("width") or 512),
        int(payload.parameters.get("height") or 512),
        int(payload.parameters.get("num_inference_steps") or 4),
        float(payload.parameters.get("guidance_scale") or 0.0),
        int(payload.parameters.get("max_sequence_length") or 256),
        float(payload.parameters.get("strength") or 0.75),
    )


def _store(app: FastAPI, payload: GenerateRequest, data: bytes, token: object, started: float) -> None:
    if not _current(app, payload.job_id, token):
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


async def run_job(app: FastAPI, payload: GenerateRequest, token: object) -> None:
    await run_batch(app, [payload], token)


async def run_batch(app: FastAPI, payloads: list[GenerateRequest], token: object) -> None:
    started = time.perf_counter()
    try:
        grouped: dict[tuple[int, int, int, float, int, float], list[GenerateRequest]] = {}
        references: dict[str, bytes | None] = {}
        for payload in payloads:
            try:
                references[payload.job_id] = _reference(app, payload)
            except StorageError:
                fail(
                    app,
                    payload.job_id,
                    token,
                    "invalid_parameters",
                    False,
                    "Reference image is missing.",
                )
                continue
            grouped.setdefault(_shape(payload), []).append(payload)
        engine: FluxEngine = app.state.engine
        for shape, group in grouped.items():
            width, height, steps, guidance, sequence, strength = shape
            images = await asyncio.to_thread(
                engine.generate_prompts,
                [item.prompt for item in group],
                width,
                height,
                steps,
                guidance,
                sequence,
                strength,
                [references[item.job_id] for item in group],
            )
            for payload, data in zip(group, images, strict=True):
                _store(app, payload, data, token, started)
    except Exception as exc:
        logger.exception("flux batch failed")
        oom = exc.__class__.__name__ == "OutOfMemoryError"
        for payload in payloads:
            fail(
                app,
                payload.job_id,
                token,
                "cuda_oom" if oom else "model_crash",
                not oom,
                "The GPU ran out of memory." if oom else "Flux failed while generating.",
            )
    finally:
        await publish_heartbeat(app)


def _current(app: FastAPI, job_id: str, token: object) -> bool:
    state = app.state.jobs.get(job_id)
    return state is not None and state.token is token


def fail(
    app: FastAPI, job_id: str, token: object, code: str, retryable: bool, message: str
) -> None:
    if not _current(app, job_id, token):
        return
    state = app.state.jobs[job_id]
    state.status = "failed"
    state.error_code = code
    state.retryable = retryable
    state.error_message = message


def app() -> FastAPI:
    return create_app()
