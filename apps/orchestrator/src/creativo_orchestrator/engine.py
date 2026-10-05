"""Dispatch loop.

The orchestrator is the only Redis consumer. Workers are push-only HTTP servers.
Postgres remains the source of truth: a reconciler re-publishes any queued job
whose Redis marker was lost.
"""

import asyncio
import logging
from dataclasses import dataclass
from datetime import timedelta

import httpx
from redis.asyncio import Redis
from sqlalchemy import or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from creativo_common.ids import new_id
from creativo_common.keys import (
    enqueued_key,
    lease_key,
    processing_key,
    queue_key,
    worker_key,
    workers_model_key,
)
from creativo_common.settings import PlatformSettings
from creativo_common.storage import FileStorage
from creativo_common.timeutil import utcnow
from creativo_common.worker_auth import signature_headers
from creativo_contracts.enums import TERMINAL_GENERATION_STATUSES, is_retryable
from creativo_contracts.worker import (
    GenerateBatchRequest,
    GenerateRequest,
    ImageInput,
    WorkerHeartbeat,
    WorkerInputs,
    WorkerJobView,
)
from creativo_db.ledger import consume, refund
from creativo_db.models import (
    Asset,
    Generation,
    GenerationInput,
    GenerationOutput,
    Job,
    JobAttempt,
    ModelVersion,
    SafetyEvent,
)
from creativo_orchestrator.scheduler import choose_worker, pack_limit

logger = logging.getLogger(__name__)
LEASE_SECONDS = 30


@dataclass
class _Claim:
    job_id: str
    attempt_id: str
    timeout: int
    payload: GenerateRequest


def _text(value: str | bytes | None) -> str | None:
    if value is None:
        return None
    return value.decode() if isinstance(value, bytes) else value


class Dispatcher:
    def __init__(
        self,
        *,
        settings: PlatformSettings,
        redis: Redis,
        sessions: async_sessionmaker[AsyncSession],
        storage: FileStorage,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self.settings = settings
        self.redis = redis
        self.sessions = sessions
        self.storage = storage
        self.client = client or httpx.AsyncClient(timeout=httpx.Timeout(10.0, connect=5.0))

    async def aclose(self) -> None:
        await self.client.aclose()

    async def dispatch_once(self) -> bool:
        for model_id in await self._models():
            workers = await self._workers(model_id)
            worker = choose_worker(workers, model_id)
            if worker is None:
                continue
            job_ids = await self._take_batch(model_id, pack_limit(worker))
            if not job_ids:
                continue
            if len(job_ids) == 1:
                await self._handle(model_id, job_ids[0])
            else:
                await self._handle_batch(model_id, job_ids, worker)
            return True
        return False

    async def reconcile(self) -> int:
        published = 0
        now = utcnow()
        async with self.sessions() as session:
            rows = (
                await session.execute(
                    select(Job.id, Job.model_id).where(
                        Job.status == "queued",
                        or_(Job.not_before.is_(None), Job.not_before <= now),
                    )
                )
            ).all()
        for job_id, model_id in rows:
            if await self.redis.get(enqueued_key(job_id)):
                continue
            processing = await self.redis.lrange(processing_key(model_id), 0, -1)
            if job_id in processing:
                continue
            await self.redis.lpush(queue_key(model_id), job_id)
            await self.redis.set(enqueued_key(job_id), "1", ex=86_400)
            published += 1
        return published

    async def recover(self) -> None:
        now = utcnow()
        for model_id in await self._models():
            for raw in await self.redis.lrange(processing_key(model_id), 0, -1):
                job_id = _text(raw)
                if job_id and not await self.redis.exists(lease_key(job_id)):
                    await self._requeue_lost(model_id, job_id)
        async with self.sessions() as session:
            rows = list(
                await session.scalars(
                    select(Job).where(
                        Job.status == "running",
                        or_(Job.lease_expires_at.is_(None), Job.lease_expires_at < now),
                    )
                )
            )
        for job in rows:
            if await self.redis.exists(lease_key(job.id)):
                continue
            await self._requeue_lost(job.model_id, job.id)

    async def _models(self) -> list[str]:
        found: list[str] = []
        async for key in self.redis.scan_iter(match="workers:model:*"):
            text_key = _text(key) or ""
            model_id = text_key.removeprefix("workers:model:")
            if model_id:
                found.append(model_id)
        return sorted(set(found))

    async def _workers(self, model_id: str) -> list[WorkerHeartbeat]:
        heartbeats: list[WorkerHeartbeat] = []
        for worker_id in await self.redis.smembers(workers_model_key(model_id)):
            raw = await self.redis.get(worker_key(_text(worker_id) or ""))
            if raw is None:
                await self.redis.srem(workers_model_key(model_id), worker_id)
                continue
            heartbeats.append(WorkerHeartbeat.model_validate_json(raw))
        return heartbeats

    async def _take_batch(self, model_id: str, limit: int) -> list[str]:
        """Pull queued prompts that can share one GPU pass. A lone job is not delayed."""
        first = _text(
            await self.redis.lmove(
                queue_key(model_id),
                processing_key(model_id),
                "LEFT",
                "RIGHT",
            )
        )
        if not first:
            return []
        batch = [first]
        if limit <= 1:
            return batch
        deadline = asyncio.get_running_loop().time() + max(0.0, self.settings.batch_fill_seconds)
        while len(batch) < limit:
            nxt = _text(
                await self.redis.lmove(
                    queue_key(model_id),
                    processing_key(model_id),
                    "LEFT",
                    "RIGHT",
                )
            )
            if nxt:
                batch.append(nxt)
                continue
            if asyncio.get_running_loop().time() >= deadline:
                break
            await asyncio.sleep(0.02)
        return batch

    async def _handle_batch(
        self, model_id: str, job_ids: list[str], worker: WorkerHeartbeat
    ) -> None:
        claimed: list[_Claim] = []
        for job_id in job_ids:
            item = await self._claim(model_id, job_id)
            if item is not None:
                claimed.append(item)
        if not claimed:
            return
        for item in claimed:
            await self._set_worker(item.attempt_id, worker.worker_id)
        body = GenerateBatchRequest(jobs=[item.payload for item in claimed]).model_dump_json().encode()
        lead = claimed[0].payload.job_id
        headers = signature_headers(self.settings.worker_shared_secret, lead, body)
        try:
            response = await self.client.post(
                worker.advertise_url.rstrip("/") + "/generate-batch",
                content=body,
                headers={**headers, "Content-Type": "application/json"},
            )
        except httpx.HTTPError as exc:
            for item in claimed:
                await self._retry(model_id, item.job_id, item.attempt_id, "worker_unreachable", str(exc))
            return
        if response.status_code == 429:
            for item in claimed:
                await self._undo_busy(model_id, item.job_id, item.attempt_id)
            return
        if response.status_code >= 400:
            detail = response.text[:300]
            for item in claimed:
                await self._retry(model_id, item.job_id, item.attempt_id, "worker_unreachable", detail)
            return
        timeout = max(item.timeout for item in claimed)
        pending = {item.job_id: item for item in claimed}
        deadline = asyncio.get_running_loop().time() + timeout
        while pending and asyncio.get_running_loop().time() < deadline:
            for item in pending.values():
                await self.redis.expire(lease_key(item.job_id), LEASE_SECONDS)
            for job_id, item in list(pending.items()):
                view = await self._poll(worker.advertise_url, job_id)
                if view is None:
                    continue
                if view.status == "completed" and (view.outputs or view.output is not None):
                    await self._complete(model_id, job_id, item.attempt_id, worker.worker_id, view)
                    pending.pop(job_id, None)
                elif view.status == "failed":
                    code = view.error_code or "model_crash"
                    message = view.error_message or code
                    if is_retryable(code, view.retryable):
                        await self._retry(model_id, job_id, item.attempt_id, code, message)
                    else:
                        await self._finish_failure(model_id, job_id, item.attempt_id, code, message)
                    pending.pop(job_id, None)
            if pending:
                await asyncio.sleep(0.3)
        for item in list(pending.values()):
            await self._retry(
                model_id, item.job_id, item.attempt_id, "worker_timeout", "Worker timed out."
            )

    async def _claim(self, model_id: str, job_id: str) -> "_Claim | None":
        acquired = await self.redis.set(
            lease_key(job_id),
            self.settings.orchestrator_id,
            nx=True,
            ex=LEASE_SECONDS,
        )
        if not acquired:
            return None
        async with self.sessions() as session:
            job = await session.get(Job, job_id)
            generation = await session.get(Generation, job.generation_id) if job else None
            if (
                job is None
                or generation is None
                or generation.status in TERMINAL_GENERATION_STATUSES
            ):
                await session.rollback()
                await self._ack(model_id, job_id)
                return None
            if generation.status == "queued":
                won = await self._mark_running(session, generation, job)
                if not won:
                    await session.commit()
                    await self._ack(model_id, job_id)
                    return None
                await session.commit()
            elif generation.status != "running":
                await session.rollback()
                await self._ack(model_id, job_id)
                return None
            else:
                await session.rollback()

        async with self.sessions() as session:
            job = await session.get(Job, job_id)
            generation = await session.get(Generation, job.generation_id) if job else None
            if job is None or generation is None:
                await self._ack(model_id, job_id)
                return None
            if generation.status in TERMINAL_GENERATION_STATUSES:
                await self._ack(model_id, job_id)
                return None
            if job.attempt_count >= job.max_attempts:
                await self._fail(
                    session, generation, job, None, "attempts_exhausted", "Generation failed."
                )
                await session.commit()
                await self._ack(model_id, job_id)
                return None
            version = await session.get(ModelVersion, generation.model_version_id)
            attempt_number = job.attempt_count + 1
            now = utcnow()
            job.attempt_count = attempt_number
            job.status = "running"
            job.lease_owner = self.settings.orchestrator_id
            job.lease_expires_at = now + timedelta(seconds=LEASE_SECONDS)
            job.updated_at = now
            attempt = JobAttempt(
                id=new_id("att"),
                job_id=job.id,
                attempt_number=attempt_number,
                worker_id=None,
                status="started",
                started_at=now,
            )
            session.add(attempt)
            images = await self._images(session, generation.id)
            await session.commit()
            timeout = version.timeout_seconds if version is not None else 60
            model_version = version.version if version is not None else "v1"
            generation_id = generation.id
            prompt = generation.prompt_final or generation.prompt_raw
            gen_type = generation.type
            mode = generation.mode
            parameters = dict(generation.parameters)
            attempt_id = attempt.id

        return _Claim(
            job_id=job_id,
            attempt_id=attempt_id,
            timeout=timeout,
            payload=GenerateRequest(
                job_id=job_id,
                generation_id=generation_id,
                attempt=attempt_number,
                model=model_id,
                model_version=model_version,
                type=gen_type,  # type: ignore[arg-type]
                mode=mode,  # type: ignore[arg-type]
                prompt=prompt,
                inputs=WorkerInputs(images=images),
                parameters=parameters,
            ),
        )

    async def _handle(self, model_id: str, job_id: str) -> None:
        claimed = await self._claim(model_id, job_id)
        if claimed is None:
            return
        attempt_id = claimed.attempt_id
        timeout = claimed.timeout
        payload = claimed.payload
        workers = await self._workers(model_id)
        worker = choose_worker(workers, model_id)
        if worker is None:
            await self._retry(
                model_id, job_id, attempt_id, "worker_unreachable", "No ready worker."
            )
            return
        await self._set_worker(attempt_id, worker.worker_id)
        body = payload.model_dump_json().encode()
        headers = signature_headers(self.settings.worker_shared_secret, job_id, body)
        try:
            response = await self.client.post(
                worker.advertise_url.rstrip("/") + "/generate",
                content=body,
                headers={**headers, "Content-Type": "application/json"},
            )
        except httpx.HTTPError as exc:
            await self._retry(model_id, job_id, attempt_id, "worker_unreachable", str(exc))
            return
        if response.status_code == 429:
            await self._undo_busy(model_id, job_id, attempt_id)
            return
        if response.status_code >= 400:
            await self._retry(
                model_id, job_id, attempt_id, "worker_unreachable", response.text[:300]
            )
            return

        deadline = asyncio.get_running_loop().time() + timeout
        while asyncio.get_running_loop().time() < deadline:
            await self.redis.expire(lease_key(job_id), LEASE_SECONDS)
            view = await self._poll(worker.advertise_url, job_id)
            if view is None:
                await asyncio.sleep(0.3)
                continue
            if view.status == "completed" and (view.outputs or view.output is not None):
                await self._complete(model_id, job_id, attempt_id, worker.worker_id, view)
                return
            if view.status == "failed":
                code = view.error_code or "model_crash"
                if is_retryable(code, view.retryable):
                    await self._retry(
                        model_id, job_id, attempt_id, code, view.error_message or code
                    )
                else:
                    await self._finish_failure(
                        model_id, job_id, attempt_id, code, view.error_message or code
                    )
                return
            await asyncio.sleep(0.3)
        await self._retry(model_id, job_id, attempt_id, "worker_timeout", "Worker timed out.")

    async def _poll(self, base_url: str, job_id: str) -> WorkerJobView | None:
        headers = signature_headers(self.settings.worker_shared_secret, job_id, b"")
        try:
            response = await self.client.get(
                base_url.rstrip("/") + f"/jobs/{job_id}",
                headers=headers,
            )
        except httpx.HTTPError:
            return None
        if response.status_code == 404:
            return None
        if response.status_code >= 400:
            return None
        return WorkerJobView.model_validate_json(response.content)

    async def _images(self, session: AsyncSession, generation_id: str) -> list[ImageInput]:
        rows = list(
            await session.scalars(
                select(GenerationInput)
                .where(GenerationInput.generation_id == generation_id)
                .order_by(GenerationInput.position)
            )
        )
        images: list[ImageInput] = []
        for row in rows:
            asset = await session.get(Asset, row.asset_id)
            if asset is not None:
                images.append(ImageInput(asset_id=asset.id, storage_key=asset.storage_key))
        return images

    async def _mark_running(self, session: AsyncSession, generation: Generation, job: Job) -> bool:
        now = utcnow()
        updated = (
            await session.execute(
                update(Generation)
                .where(Generation.id == generation.id, Generation.status == "queued")
                .values(status="running", updated_at=now)
                .returning(Generation.id)
            )
        ).first()
        if updated is None:
            return False
        job.status = "running"
        job.lease_owner = self.settings.orchestrator_id
        job.lease_expires_at = now + timedelta(seconds=LEASE_SECONDS)
        job.updated_at = now
        return True

    async def _complete(
        self,
        model_id: str,
        job_id: str,
        attempt_id: str,
        worker_id: str,
        view: WorkerJobView,
    ) -> None:
        frames = list(view.outputs) or ([view.output] if view.output is not None else [])
        async with self.sessions() as session:
            job = await session.get(Job, job_id)
            generation = await session.get(Generation, job.generation_id) if job else None
            if job is None or generation is None:
                await self._ack(model_id, job_id)
                return
            prefix = f"outputs/{generation.id}/"
            if not frames or any(
                not frame.storage_key.startswith(prefix)
                or ".." in frame.storage_key
                or not self.storage.exists(frame.storage_key)
                for frame in frames
            ):
                await self._retry(
                    model_id, job_id, attempt_id, "storage_failed", "Output was not stored."
                )
                return
            now = utcnow()
            updated = (
                await session.execute(
                    update(Generation)
                    .where(Generation.id == generation.id, Generation.status == "running")
                    .values(status="completed", updated_at=now, completed_at=now)
                    .returning(Generation.id)
                )
            ).first()
            if updated is None:
                await session.rollback()
                await self._ack(model_id, job_id)
                return
            gpu_millis = int(round((view.usage.gpu_seconds if view.usage else 0) * 1000))
            for index, frame in enumerate(frames):
                session.add(
                    GenerationOutput(
                        id=new_id("out"),
                        generation_id=generation.id,
                        position=index,
                        storage_key=frame.storage_key,
                        content_type=frame.content_type,
                        byte_size=frame.byte_size,
                        gpu_millis=gpu_millis if index == 0 else 0,
                        created_at=now,
                    )
                )
            session.add(
                SafetyEvent(
                    id=new_id("saf"),
                    user_id=generation.user_id,
                    generation_id=generation.id,
                    category=None,
                    action="skipped",
                    source="output_moderation",
                    rule_id=None,
                    prompt_hash="0" * 64,
                    detail={"reason": "no output classifier configured"},
                    created_at=now,
                )
            )
            await consume(session, user_id=generation.user_id, generation_id=generation.id)
            job.status = "completed"
            job.updated_at = now
            attempt = await session.get(JobAttempt, attempt_id)
            if attempt is not None:
                attempt.status = "succeeded"
                attempt.worker_id = worker_id
                attempt.finished_at = now
            await session.commit()
        await self._ack(model_id, job_id)
        logger.info(
            "generation completed",
            extra={
                "generation_id": generation.id,
                "job_id": job_id,
                "worker_id": worker_id,
                "model_id": model_id,
            },
        )

    async def _retry(
        self,
        model_id: str,
        job_id: str,
        attempt_id: str,
        code: str,
        message: str,
    ) -> None:
        async with self.sessions() as session:
            job = await session.get(Job, job_id)
            generation = await session.get(Generation, job.generation_id) if job else None
            attempt = await session.get(JobAttempt, attempt_id)
            if job is None or generation is None:
                await self._ack(model_id, job_id)
                return
            now = utcnow()
            if attempt is not None:
                attempt.status = "retryable_failure"
                attempt.error_code = code
                attempt.error_message = message[:500]
                attempt.finished_at = now
            if (
                job.attempt_count >= job.max_attempts
                or generation.status in TERMINAL_GENERATION_STATUSES
            ):
                await self._fail(session, generation, job, attempt, code, message)
                await session.commit()
                await self._ack(model_id, job_id)
                return
            job.status = "queued"
            job.not_before = now + timedelta(seconds=self.settings.retry_delay_seconds)
            job.lease_owner = None
            job.lease_expires_at = None
            job.updated_at = now
            await session.commit()
        await self.redis.lrem(processing_key(model_id), 1, job_id)
        await self.redis.delete(lease_key(job_id), enqueued_key(job_id))

    async def _finish_failure(
        self,
        model_id: str,
        job_id: str,
        attempt_id: str,
        code: str,
        message: str,
    ) -> None:
        async with self.sessions() as session:
            job = await session.get(Job, job_id)
            generation = await session.get(Generation, job.generation_id) if job else None
            attempt = await session.get(JobAttempt, attempt_id)
            if job is None or generation is None:
                await self._ack(model_id, job_id)
                return
            await self._fail(session, generation, job, attempt, code, message)
            await session.commit()
        await self._ack(model_id, job_id)

    async def _fail(
        self,
        session: AsyncSession,
        generation: Generation,
        job: Job,
        attempt: JobAttempt | None,
        code: str,
        message: str,
    ) -> None:
        now = utcnow()
        updated = (
            await session.execute(
                update(Generation)
                .where(Generation.id == generation.id, Generation.status.in_(("running", "queued")))
                .values(
                    status="failed",
                    failure_code=code,
                    failure_message="The generation failed before it could finish.",
                    updated_at=now,
                    completed_at=now,
                )
                .returning(Generation.id)
            )
        ).first()
        job.status = "failed"
        job.updated_at = now
        if attempt is not None:
            attempt.status = "permanent_failure"
            attempt.error_code = code
            attempt.error_message = message[:500]
            attempt.finished_at = now
        if updated is not None:
            await refund(
                session,
                user_id=generation.user_id,
                generation_id=generation.id,
                description="Released after failure",
            )

    async def _undo_busy(self, model_id: str, job_id: str, attempt_id: str) -> None:
        async with self.sessions() as session:
            job = await session.get(Job, job_id)
            attempt = await session.get(JobAttempt, attempt_id)
            if job is not None:
                job.attempt_count = max(0, job.attempt_count - 1)
                job.status = "queued"
                job.not_before = utcnow() + timedelta(seconds=self.settings.retry_delay_seconds)
                job.updated_at = utcnow()
            if attempt is not None:
                await session.delete(attempt)
            await session.commit()
        await self.redis.lrem(processing_key(model_id), 1, job_id)
        await self.redis.delete(lease_key(job_id), enqueued_key(job_id))

    async def _set_worker(self, attempt_id: str, worker_id: str) -> None:
        async with self.sessions() as session:
            attempt = await session.get(JobAttempt, attempt_id)
            if attempt is not None:
                attempt.worker_id = worker_id
                await session.commit()

    async def _requeue_lost(self, model_id: str, job_id: str) -> None:
        await self.redis.lrem(processing_key(model_id), 1, job_id)
        await self.redis.delete(lease_key(job_id), enqueued_key(job_id))
        async with self.sessions() as session:
            job = await session.get(Job, job_id)
            if job is None or job.status in {"completed", "failed", "cancelled"}:
                return
            generation = await session.get(Generation, job.generation_id)
            if generation is None or generation.status in TERMINAL_GENERATION_STATUSES:
                return
            job.status = "queued"
            job.not_before = utcnow()
            job.lease_owner = None
            job.lease_expires_at = None
            job.updated_at = utcnow()
            await session.commit()

    async def _ack(self, model_id: str, job_id: str) -> None:
        await self.redis.lrem(processing_key(model_id), 1, job_id)
        await self.redis.delete(lease_key(job_id), enqueued_key(job_id))
