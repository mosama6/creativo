import hashlib
import json
from base64 import urlsafe_b64decode, urlsafe_b64encode
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import select, text, tuple_, update
from sqlalchemy.ext.asyncio import AsyncSession

from creativo_api.adapters import adapter_for
from creativo_api.errors import AppError
from creativo_api.policy import decide, prompt_hash, rejection_error
from creativo_api.prompts import PassthroughEnhancer, PromptEnhancer, prepare_prompt
from creativo_api.security import IDEMPOTENCY_RE
from creativo_common.ids import new_id
from creativo_common.settings import PlatformSettings
from creativo_common.timeutil import utcnow
from creativo_contracts.generations import (
    CreateGenerationRequest,
    GenerationOutputView,
    GenerationPage,
    GenerationView,
)
from creativo_db.errors import InsufficientCredits
from creativo_db.ledger import refund, reserve
from creativo_db.models import (
    Asset,
    CatalogModel,
    Generation,
    GenerationInput,
    GenerationOutput,
    IdempotencyKey,
    Job,
    ModelVersion,
    SafetyEvent,
    User,
)


@dataclass
class CreateOutcome:
    view: GenerationView | None = None
    error: AppError | None = None
    publish_model_id: str | None = None
    publish_job_id: str | None = None


def canonical_hash(body: CreateGenerationRequest) -> str:
    raw = json.dumps(body.model_dump(mode="json"), sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(raw.encode()).hexdigest()


def _lock_key(user_id: str, key: str) -> int:
    digest = hashlib.sha256(f"{user_id}:{key}".encode()).digest()
    return int.from_bytes(digest[:8], "big", signed=True)


def _encode_cursor(created_at: datetime, generation_id: str) -> str:
    raw = f"{created_at.isoformat()}|{generation_id}".encode()
    return urlsafe_b64encode(raw).decode()


def _decode_cursor(cursor: str) -> tuple[datetime, str]:
    try:
        raw = urlsafe_b64decode(cursor.encode()).decode()
        stamp, generation_id = raw.split("|", 1)
        created_at = datetime.fromisoformat(stamp)
    except (ValueError, UnicodeError) as exc:
        raise AppError("invalid_cursor", "That page cursor is not valid.", 400) from exc
    return created_at, generation_id


async def _view(db: AsyncSession, generation: Generation) -> GenerationView:
    rows = list(
        await db.scalars(
            select(GenerationOutput)
            .where(GenerationOutput.generation_id == generation.id)
            .order_by(GenerationOutput.position)
        )
    )
    outputs = [
        GenerationOutputView(
            index=row.position,
            content_type=row.content_type,
            byte_size=row.byte_size,
            gpu_millis=row.gpu_millis,
        )
        for row in rows
    ]
    return GenerationView(
        id=generation.id,
        status=generation.status,  # type: ignore[arg-type]
        type=generation.type,  # type: ignore[arg-type]
        mode=generation.mode,  # type: ignore[arg-type]
        model=generation.model_id,
        prompt=generation.prompt_raw,
        prompt_final=generation.prompt_final,
        parameters=generation.parameters,
        credit_price=generation.credit_price,
        failure_code=generation.failure_code,
        failure_message=generation.failure_message,
        output=outputs[0] if outputs else None,
        outputs=outputs,
        created_at=generation.created_at,
        updated_at=generation.updated_at,
        completed_at=generation.completed_at,
    )


async def _owned(db: AsyncSession, user_id: str, generation_id: str) -> Generation:
    generation = await db.get(Generation, generation_id)
    if generation is None or generation.user_id != user_id:
        raise AppError("generation_not_found", "Generation not found.", 404)
    return generation


async def create_generation(
    db: AsyncSession,
    *,
    user: User,
    body: CreateGenerationRequest,
    idempotency_key: str | None,
    request_id: str,
    settings: PlatformSettings,
    enhancer: PromptEnhancer | None = None,
) -> CreateOutcome:
    if not idempotency_key or not IDEMPOTENCY_RE.match(idempotency_key):
        return CreateOutcome(
            error=AppError(
                "idempotency_key_required",
                "Send an Idempotency-Key header between 8 and 200 characters.",
                400,
            )
        )
    if body.model == "auto":
        return CreateOutcome(
            error=AppError(
                "model_selection_required",
                "Choose a model. Automatic routing is not enabled.",
                422,
            )
        )

    await db.execute(
        text("SELECT pg_advisory_xact_lock(:key)"),
        {"key": _lock_key(user.id, idempotency_key)},
    )
    digest = canonical_hash(body)
    existing = await db.scalar(
        select(IdempotencyKey).where(
            IdempotencyKey.user_id == user.id,
            IdempotencyKey.key == idempotency_key,
        )
    )
    now = utcnow()
    if existing is not None and existing.expires_at <= now:
        await db.delete(existing)
        await db.flush()
        existing = None
    if existing is not None:
        if existing.request_hash != digest:
            return CreateOutcome(
                error=AppError(
                    "idempotency_key_reused",
                    "That idempotency key was already used for a different request.",
                    409,
                )
            )
        if existing.generation_id is None:
            return CreateOutcome(
                error=AppError(
                    "idempotency_in_progress", "That request is already being submitted.", 409
                )
            )
        generation = await db.get(Generation, existing.generation_id)
        if generation is None:
            return CreateOutcome(
                error=AppError("generation_not_found", "Generation not found.", 404)
            )
        view = await _view(db, generation)
        if generation.status == "rejected":
            event = await db.scalar(
                select(SafetyEvent).where(
                    SafetyEvent.generation_id == generation.id,
                    SafetyEvent.action == "block",
                )
            )
            category = event.category if event is not None else None
            return CreateOutcome(view=view, error=rejection_error(generation.id, category))
        return CreateOutcome(view=view)

    model = await db.get(CatalogModel, body.model)
    version = await db.scalar(
        select(ModelVersion).where(
            ModelVersion.model_id == body.model,
            ModelVersion.status == "active",
        )
    )
    if model is None or not model.enabled or model.status != "active" or version is None:
        return CreateOutcome(
            error=AppError("model_unavailable", "That model is not available.", 422)
        )
    adapter = adapter_for(model.id)
    if adapter is None:
        return CreateOutcome(
            error=AppError("model_unavailable", "That model is not available.", 422)
        )
    try:
        adapter.validate(body, version.capabilities)
    except AppError as exc:
        return CreateOutcome(error=exc)

    assets: list[Asset] = []
    for asset_id in body.reference_images:
        asset = await db.get(Asset, asset_id)
        if asset is None or asset.user_id != user.id or asset.deleted_at is not None:
            return CreateOutcome(
                error=AppError("asset_not_found", "A reference image was not found.", 404)
            )
        assets.append(asset)

    decision = await decide(body.prompt, sfw_only=settings.sfw_only)
    prompt_enhancer = enhancer or PassthroughEnhancer()
    prepared = await prepare_prompt(decision, prompt_enhancer, body.prompt)
    hashed = prompt_hash(body.prompt)

    if decision.action == "block":
        generation = Generation(
            id=new_id("gen"),
            user_id=user.id,
            request_id=request_id,
            status="rejected",
            type=body.type,
            mode=body.mode,
            model_id=model.id,
            model_version_id=version.id,
            prompt_raw=body.prompt,
            prompt_final=None,
            prompt_ir=None,
            parameters={},
            credit_price=0,
            failure_code="content_rejected",
            failure_message="This request was blocked by the safety policy.",
            created_at=now,
            updated_at=now,
            completed_at=now,
        )
        db.add(generation)
        await db.flush()
        db.add(
            SafetyEvent(
                id=new_id("saf"),
                user_id=user.id,
                generation_id=generation.id,
                category=decision.category,
                action="block",
                source=decision.source,
                rule_id=decision.rule_id,
                prompt_hash=hashed,
                detail={"category": decision.category},
                created_at=now,
            )
        )
        _remember(
            db, user.id, idempotency_key, digest, generation.id, "content_rejected", now, settings
        )
        await db.flush()
        return CreateOutcome(
            view=await _view(db, generation),
            error=rejection_error(generation.id, decision.category),
        )

    price = adapter.estimate_cost(body, version.credit_cost)
    parameters = adapter.build_parameters(body)
    generation = Generation(
        id=new_id("gen"),
        user_id=user.id,
        request_id=request_id,
        status="queued",
        type=body.type,
        mode=body.mode,
        model_id=model.id,
        model_version_id=version.id,
        prompt_raw=body.prompt,
        prompt_final=prepared.final,
        prompt_ir=prepared.ir,
        parameters=parameters,
        credit_price=price,
        failure_code=None,
        failure_message=None,
        created_at=now,
        updated_at=now,
    )
    db.add(generation)
    await db.flush()
    for position, asset in enumerate(assets):
        db.add(
            GenerationInput(
                id=new_id("gin"),
                generation_id=generation.id,
                asset_id=asset.id,
                position=position,
            )
        )
    job = Job(
        id=new_id("job"),
        generation_id=generation.id,
        model_id=model.id,
        status="queued",
        attempt_count=0,
        max_attempts=version.max_attempts,
        not_before=None,
        created_at=now,
        updated_at=now,
    )
    db.add(job)
    db.add(
        SafetyEvent(
            id=new_id("saf"),
            user_id=user.id,
            generation_id=generation.id,
            category=None,
            action="allow",
            source="rules",
            rule_id=None,
            prompt_hash=hashed,
            detail={},
            created_at=now,
        )
    )
    try:
        await reserve(db, user_id=user.id, generation_id=generation.id, amount=price)
    except InsufficientCredits as exc:
        raise AppError(
            "insufficient_credits",
            "Not enough available credits for this generation.",
            402,
        ) from exc
    _remember(db, user.id, idempotency_key, digest, generation.id, None, now, settings)
    await db.flush()
    return CreateOutcome(
        view=await _view(db, generation),
        publish_model_id=model.id,
        publish_job_id=job.id,
    )


def _remember(
    db: AsyncSession,
    user_id: str,
    key: str,
    digest: str,
    generation_id: str,
    error_code: str | None,
    now: datetime,
    settings: PlatformSettings,
) -> None:
    from datetime import timedelta

    db.add(
        IdempotencyKey(
            id=new_id("idem"),
            user_id=user_id,
            key=key,
            request_hash=digest,
            generation_id=generation_id,
            error_code=error_code,
            error_message=None,
            created_at=now,
            expires_at=now + timedelta(seconds=settings.idempotency_ttl_seconds),
        )
    )


async def get_generation(db: AsyncSession, user: User, generation_id: str) -> GenerationView:
    return await _view(db, await _owned(db, user.id, generation_id))


async def list_generations(
    db: AsyncSession,
    user: User,
    *,
    cursor: str | None,
    limit: int,
) -> GenerationPage:
    stmt = (
        select(Generation)
        .where(Generation.user_id == user.id)
        .order_by(Generation.created_at.desc(), Generation.id.desc())
        .limit(limit + 1)
    )
    if cursor:
        created_at, generation_id = _decode_cursor(cursor)
        stmt = stmt.where(
            tuple_(Generation.created_at, Generation.id) < tuple_(created_at, generation_id)
        )
    rows = list(await db.scalars(stmt))
    page = rows[:limit]
    next_cursor = None
    if len(rows) > limit and page:
        last = page[-1]
        next_cursor = _encode_cursor(last.created_at, last.id)
    return GenerationPage(items=[await _view(db, row) for row in page], next_cursor=next_cursor)


async def cancel_generation(db: AsyncSession, user: User, generation_id: str) -> GenerationView:
    generation = await _owned(db, user.id, generation_id)
    if generation.status == "cancelled":
        return await _view(db, generation)
    now = utcnow()
    updated = (
        await db.execute(
            update(Generation)
            .where(
                Generation.id == generation.id,
                Generation.user_id == user.id,
                Generation.status == "queued",
            )
            .values(status="cancelled", updated_at=now, completed_at=now)
            .returning(Generation.id)
        )
    ).first()
    if updated is None:
        current = await db.get(Generation, generation.id)
        status = current.status if current is not None else generation.status
        raise AppError(
            "cancel_not_allowed",
            "Cancel is available while the generation is still queued.",
            409,
            {"status": status},
        )
    await db.execute(
        update(Job)
        .where(Job.generation_id == generation.id, Job.status == "queued")
        .values(status="cancelled", updated_at=now)
    )
    await refund(
        db,
        user_id=user.id,
        generation_id=generation.id,
        description="Released after cancel",
    )
    await db.refresh(generation)
    return await _view(db, generation)


async def output_for(
    db: AsyncSession, user: User, generation_id: str, index: int = 0
) -> GenerationOutput:
    await _owned(db, user.id, generation_id)
    output = await db.scalar(
        select(GenerationOutput).where(
            GenerationOutput.generation_id == generation_id,
            GenerationOutput.position == index,
        )
    )
    if output is None:
        raise AppError("output_not_ready", "This generation has no output yet.", 404)
    return output
