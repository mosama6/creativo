import logging
from urllib.parse import urlencode

import httpx
from fastapi import APIRouter, Depends, File, Query, Request, Response, UploadFile
from fastapi.responses import RedirectResponse
from fastapi.responses import Response as RawResponse
from pydantic import BaseModel, Field
from redis.asyncio import Redis
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from creativo_api.assets import delete_asset, get_asset, list_assets, store_upload
from creativo_api.broker import enforce_rate, publish_job
from creativo_api.errors import AppError
from creativo_api.generation_service import (
    cancel_generation,
    create_generation,
    get_generation,
    list_generations,
    output_for,
)
from creativo_api.security import (
    clear_session_cookie,
    issue_session,
    require_client,
    set_session_cookie,
    upsert_user,
    user_from_request,
)
from creativo_common.keys import oauth_state_key
from creativo_common.settings import PlatformSettings
from creativo_common.storage import FileStorage, StorageError
from creativo_contracts.api import AssetView, AuthProviders, ModelVersionView, ModelView, UserView
from creativo_contracts.credits import (
    CreditAccountView,
    CreditTransactionPage,
    CreditTransactionView,
)
from creativo_contracts.generations import CreateGenerationRequest, GenerationPage, GenerationView
from creativo_db.ledger import ensure_account
from creativo_db.models import CatalogModel, CreditAccount, CreditTransaction, ModelVersion, User

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1")
GOOGLE_AUTHORIZE = "https://accounts.google.com/o/oauth2/v2/auth"
GOOGLE_TOKEN = "https://oauth2.googleapis.com/token"
GOOGLE_USERINFO = "https://openidconnect.googleapis.com/v1/userinfo"


def get_db(request: Request) -> AsyncSession:
    return request.state.db


async def db_session(request: Request):
    factory = request.app.state.session_factory
    async with factory() as session:
        request.state.db = session
        try:
            yield session
        finally:
            await session.close()


def settings_of(request: Request) -> PlatformSettings:
    return request.app.state.settings


def redis_of(request: Request) -> Redis:
    return request.app.state.redis


def storage_of(request: Request) -> FileStorage:
    return request.app.state.storage


Db = Depends(db_session)


def _user_view(user: User) -> UserView:
    return UserView(
        id=user.id,
        email=user.email,
        name=user.name,
        avatar_url=user.avatar_url,
        created_at=user.created_at,
    )


@router.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok", "service": "api"}


@router.get("/auth/providers", response_model=AuthProviders)
async def providers(settings: PlatformSettings = Depends(settings_of)) -> AuthProviders:
    return AuthProviders(google=settings.google_enabled, dev_login=settings.dev_auth_enabled)


class DevLoginRequest(BaseModel):
    email: str = Field(min_length=3, max_length=320)
    name: str = ""


@router.post("/auth/dev-login", response_model=UserView)
async def dev_login(
    response: Response,
    request: Request,
    body: DevLoginRequest,
    db: AsyncSession = Db,
    settings: PlatformSettings = Depends(settings_of),
) -> UserView:
    require_client(request)
    if not settings.dev_auth_enabled:
        raise AppError("dev_login_disabled", "Development sign-in is disabled.", 404)
    email = body.email.strip().lower()
    name = body.name.strip()
    if "@" not in email or "." not in email.split("@")[-1]:
        raise AppError("invalid_request", "Enter a valid email.", 422)
    user = await upsert_user(
        db,
        email=email,
        name=name or email.split("@")[0],
        avatar_url=None,
        settings=settings,
    )
    token = await issue_session(db, user.id, settings)
    await db.commit()
    set_session_cookie(response, token, settings)
    return _user_view(user)


@router.get("/auth/google")
async def google_start(
    request: Request,
    settings: PlatformSettings = Depends(settings_of),
    redis: Redis = Depends(redis_of),
) -> RedirectResponse:
    if not settings.google_enabled:
        raise AppError("oauth_not_configured", "Google sign-in is not configured.", 503)
    import secrets

    state = secrets.token_urlsafe(24)
    await redis.set(oauth_state_key(state), "1", ex=600)
    redirect_uri = settings.api_public_url.rstrip("/") + "/api/v1/auth/google/callback"
    query = urlencode(
        {
            "client_id": settings.google_client_id,
            "redirect_uri": redirect_uri,
            "response_type": "code",
            "scope": "openid email profile",
            "state": state,
            "prompt": "select_account",
        }
    )
    return RedirectResponse(f"{GOOGLE_AUTHORIZE}?{query}")


@router.get("/auth/google/callback")
async def google_callback(
    request: Request,
    code: str = "",
    state: str = "",
    db: AsyncSession = Db,
    settings: PlatformSettings = Depends(settings_of),
    redis: Redis = Depends(redis_of),
) -> RedirectResponse:
    failure = RedirectResponse(
        settings.web_public_url.rstrip("/") + "/?error=google", status_code=302
    )
    if not code or not state:
        return failure
    stored = await redis.getdel(oauth_state_key(state))
    if not stored:
        return failure
    redirect_uri = settings.api_public_url.rstrip("/") + "/api/v1/auth/google/callback"
    try:
        async with httpx.AsyncClient(timeout=15) as client:
            token_response = await client.post(
                GOOGLE_TOKEN,
                data={
                    "code": code,
                    "client_id": settings.google_client_id,
                    "client_secret": settings.google_client_secret,
                    "redirect_uri": redirect_uri,
                    "grant_type": "authorization_code",
                },
            )
            token_response.raise_for_status()
            access = token_response.json().get("access_token")
            profile_response = await client.get(
                GOOGLE_USERINFO,
                headers={"Authorization": f"Bearer {access}"},
            )
            profile_response.raise_for_status()
            profile = profile_response.json()
    except httpx.HTTPError:
        logger.exception("google oauth exchange failed")
        return failure
    subject = profile.get("sub")
    email = str(profile.get("email", "")).strip().lower()
    if not subject or not email:
        return failure
    user = await upsert_user(
        db,
        email=email,
        name=str(profile.get("name") or ""),
        avatar_url=profile.get("picture"),
        settings=settings,
        provider="google",
        provider_subject=subject,
    )
    token = await issue_session(db, user.id, settings)
    await db.commit()
    redirect = RedirectResponse(settings.web_public_url.rstrip("/") + "/app", status_code=302)
    set_session_cookie(redirect, token, settings)
    return redirect


@router.post("/auth/logout")
async def logout(
    request: Request,
    response: Response,
    db: AsyncSession = Db,
    settings: PlatformSettings = Depends(settings_of),
) -> dict[str, str]:
    require_client(request)
    user = await user_from_request(request, db)
    from creativo_api.security import SESSION_COOKIE, token_hash
    from creativo_db.models import Session as SessionRow

    raw = request.cookies.get(SESSION_COOKIE)
    if raw:
        row = await db.scalar(select(SessionRow).where(SessionRow.token_hash == token_hash(raw)))
        if row is not None and row.user_id == user.id:
            await db.delete(row)
            await db.commit()
    clear_session_cookie(response, settings)
    return {"status": "signed_out"}


@router.get("/auth/me", response_model=UserView)
async def me(request: Request, db: AsyncSession = Db) -> UserView:
    return _user_view(await user_from_request(request, db))


@router.get("/models", response_model=list[ModelView])
async def list_models(db: AsyncSession = Db) -> list[ModelView]:
    models = list(await db.scalars(select(CatalogModel).order_by(CatalogModel.display_name)))
    versions = {row.model_id: row for row in await db.scalars(select(ModelVersion))}
    return [_model_view(model, versions.get(model.id)) for model in models]


@router.get("/models/{model_id}", response_model=ModelView)
async def get_model(model_id: str, db: AsyncSession = Db) -> ModelView:
    model = await db.get(CatalogModel, model_id)
    if model is None:
        raise AppError("model_not_found", "Model not found.", 404)
    version = await db.scalar(select(ModelVersion).where(ModelVersion.model_id == model.id))
    return _model_view(model, version)


def _model_view(model: CatalogModel, version: ModelVersion | None) -> ModelView:
    version_view = None
    if version is not None:
        version_view = ModelVersionView(
            id=version.id,
            version=version.version,
            status=version.status,
            credit_cost=version.credit_cost,
            pricing_status=version.pricing_status,
            required_vram_gb=version.required_vram_gb,
            supported_gpus=list(version.supported_gpus),
            timeout_seconds=version.timeout_seconds,
            max_attempts=version.max_attempts,
            capabilities=version.capabilities,
            container_image=version.container_image,
        )
    return ModelView(
        id=model.id,
        display_name=model.display_name,
        provider=model.provider,
        modality=model.modality,
        enabled=model.enabled,
        status=model.status,
        summary=model.summary,
        version=version_view,
    )


@router.get("/credits", response_model=CreditAccountView)
async def credits(request: Request, db: AsyncSession = Db) -> CreditAccountView:
    user = await user_from_request(request, db)
    account = await db.get(CreditAccount, user.id)
    if account is None:
        account = await ensure_account(db, user.id)
        await db.commit()
    return CreditAccountView(available=int(account.available), reserved=int(account.reserved))


@router.get("/credits/transactions", response_model=CreditTransactionPage)
async def credit_transactions(request: Request, db: AsyncSession = Db) -> CreditTransactionPage:
    user = await user_from_request(request, db)
    rows = await db.scalars(
        select(CreditTransaction)
        .where(CreditTransaction.user_id == user.id)
        .order_by(CreditTransaction.created_at.desc())
        .limit(50)
    )
    return CreditTransactionPage(
        items=[
            CreditTransactionView(
                id=row.id,
                type=row.type,  # type: ignore[arg-type]
                amount=int(row.amount),
                available_after=int(row.available_after),
                reserved_after=int(row.reserved_after),
                description=row.description,
                generation_id=row.generation_id,
                created_at=row.created_at,
            )
            for row in rows
        ]
    )


@router.post("/generations", response_model=GenerationView)
async def post_generation(
    request: Request,
    body: CreateGenerationRequest,
    db: AsyncSession = Db,
    settings: PlatformSettings = Depends(settings_of),
    redis: Redis = Depends(redis_of),
) -> GenerationView:
    require_client(request)
    user = await user_from_request(request, db)
    await enforce_rate(
        redis,
        "generations",
        user.id,
        settings.generation_rate_limit,
        settings.generation_rate_window_seconds,
    )
    try:
        outcome = await create_generation(
            db,
            user=user,
            body=body,
            idempotency_key=request.headers.get("idempotency-key"),
            request_id=getattr(request.state, "request_id", ""),
            settings=settings,
        )
    except AppError:
        await db.rollback()
        raise
    if outcome.error is not None and outcome.view is None:
        await db.rollback()
        raise outcome.error
    await db.commit()
    if outcome.error is not None:
        raise outcome.error
    if outcome.publish_job_id and outcome.publish_model_id:
        try:
            await publish_job(redis, outcome.publish_model_id, outcome.publish_job_id)
        except Exception:
            logger.exception(
                "queue publish failed",
                extra={
                    "generation_id": outcome.view.id if outcome.view else None,
                    "job_id": outcome.publish_job_id,
                },
            )
    assert outcome.view is not None
    return outcome.view


@router.get("/generations", response_model=GenerationPage)
async def get_generations(
    request: Request,
    cursor: str | None = None,
    limit: int = Query(default=24, ge=1, le=60),
    db: AsyncSession = Db,
) -> GenerationPage:
    user = await user_from_request(request, db)
    return await list_generations(db, user, cursor=cursor, limit=limit)


@router.get("/generations/{generation_id}", response_model=GenerationView)
async def get_one_generation(
    generation_id: str,
    request: Request,
    db: AsyncSession = Db,
) -> GenerationView:
    user = await user_from_request(request, db)
    return await get_generation(db, user, generation_id)


@router.post("/generations/{generation_id}/cancel", response_model=GenerationView)
async def post_cancel(
    generation_id: str,
    request: Request,
    db: AsyncSession = Db,
) -> GenerationView:
    require_client(request)
    user = await user_from_request(request, db)
    view = await cancel_generation(db, user, generation_id)
    await db.commit()
    return view


@router.get("/generations/{generation_id}/output")
async def generation_output(
    generation_id: str,
    request: Request,
    index: int = Query(default=0, ge=0, le=8),
    db: AsyncSession = Db,
    storage: FileStorage = Depends(storage_of),
) -> RawResponse:
    user = await user_from_request(request, db)
    output = await output_for(db, user, generation_id, index)
    try:
        data = storage.get(output.storage_key)
    except StorageError as exc:
        raise AppError("output_missing", "The output file is missing.", 404) from exc
    return RawResponse(content=data, media_type=output.content_type)


@router.post("/uploads", response_model=AssetView)
async def upload(
    request: Request,
    file: UploadFile = File(...),
    db: AsyncSession = Db,
    settings: PlatformSettings = Depends(settings_of),
    redis: Redis = Depends(redis_of),
    storage: FileStorage = Depends(storage_of),
) -> AssetView:
    require_client(request)
    user = await user_from_request(request, db)
    await enforce_rate(
        redis,
        "uploads",
        user.id,
        settings.upload_rate_limit,
        settings.upload_rate_window_seconds,
    )
    data = await file.read()
    view = await store_upload(db, storage, settings, user, file.filename or "upload", data)
    await db.commit()
    return view


@router.get("/assets", response_model=list[AssetView])
async def assets(request: Request, db: AsyncSession = Db) -> list[AssetView]:
    user = await user_from_request(request, db)
    return await list_assets(db, user)


@router.get("/assets/{asset_id}/content")
async def asset_content(
    asset_id: str,
    request: Request,
    db: AsyncSession = Db,
    storage: FileStorage = Depends(storage_of),
) -> RawResponse:
    user = await user_from_request(request, db)
    asset = await get_asset(db, user, asset_id)
    try:
        data = storage.get(asset.storage_key)
    except StorageError as exc:
        raise AppError("asset_not_found", "Asset not found.", 404) from exc
    return RawResponse(content=data, media_type=asset.content_type)


@router.delete("/assets/{asset_id}")
async def remove_asset(asset_id: str, request: Request, db: AsyncSession = Db) -> dict[str, str]:
    require_client(request)
    user = await user_from_request(request, db)
    await delete_asset(db, user, asset_id)
    await db.commit()
    return {"status": "deleted"}
