import hashlib
import re
import secrets
from datetime import timedelta

from fastapi import Request, Response
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from creativo_api.errors import AppError
from creativo_common.ids import new_id
from creativo_common.settings import PlatformSettings
from creativo_common.timeutil import utcnow
from creativo_db.ledger import ensure_account, grant
from creativo_db.models import OauthAccount, Session, User

SESSION_COOKIE = "creativo_session"
CLIENT_HEADER = "x-creativo-client"
IDEMPOTENCY_RE = re.compile(r"^[A-Za-z0-9_.:-]{8,200}$")


def require_client(request: Request) -> None:
    if request.headers.get(CLIENT_HEADER) != "web":
        raise AppError("missing_client_header", "Missing client header.", 403)


def token_hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def set_session_cookie(response: Response, token: str, settings: PlatformSettings) -> None:
    response.set_cookie(
        key=SESSION_COOKIE,
        value=token,
        httponly=True,
        secure=settings.app_env == "production",
        samesite="lax",
        max_age=settings.session_ttl_seconds,
        path="/",
    )


def clear_session_cookie(response: Response, settings: PlatformSettings) -> None:
    response.delete_cookie(
        key=SESSION_COOKIE,
        path="/",
        httponly=True,
        secure=settings.app_env == "production",
        samesite="lax",
    )


async def issue_session(db: AsyncSession, user_id: str, settings: PlatformSettings) -> str:
    raw = secrets.token_urlsafe(32)
    now = utcnow()
    db.add(
        Session(
            id=new_id("ses"),
            user_id=user_id,
            token_hash=token_hash(raw),
            expires_at=now + timedelta(seconds=settings.session_ttl_seconds),
            created_at=now,
        )
    )
    await db.flush()
    return raw


async def user_from_request(request: Request, db: AsyncSession) -> User:
    raw = request.cookies.get(SESSION_COOKIE)
    if not raw:
        raise AppError("unauthenticated", "Sign in required.", 401)
    row = await db.scalar(select(Session).where(Session.token_hash == token_hash(raw)))
    now = utcnow()
    if row is None or row.expires_at <= now:
        raise AppError("unauthenticated", "Sign in required.", 401)
    user = await db.get(User, row.user_id)
    if user is None or user.status != "active":
        raise AppError("unauthenticated", "Sign in required.", 401)
    return user


async def upsert_user(
    db: AsyncSession,
    *,
    email: str,
    name: str,
    avatar_url: str | None,
    settings: PlatformSettings,
    provider: str | None = None,
    provider_subject: str | None = None,
) -> User:
    now = utcnow()
    user: User | None = None
    if provider and provider_subject:
        oauth = await db.scalar(
            select(OauthAccount).where(
                OauthAccount.provider == provider,
                OauthAccount.provider_subject == provider_subject,
            )
        )
        if oauth is not None:
            user = await db.get(User, oauth.user_id)
    if user is None:
        user = await db.scalar(select(User).where(User.email == email))
    created = user is None
    if user is None:
        user = User(
            id=new_id("usr"),
            email=email,
            name=name or "",
            avatar_url=avatar_url,
            status="active",
            created_at=now,
            updated_at=now,
        )
        db.add(user)
        await db.flush()
        await ensure_account(db, user.id)
        if settings.dev_starting_credits > 0:
            await grant(
                db,
                user_id=user.id,
                amount=settings.dev_starting_credits,
                tx_type="bonus",
                description="Welcome credits",
            )
    else:
        if name:
            user.name = name
        if avatar_url:
            user.avatar_url = avatar_url
        user.updated_at = now
    if provider and provider_subject:
        existing = await db.scalar(
            select(OauthAccount).where(
                OauthAccount.provider == provider,
                OauthAccount.provider_subject == provider_subject,
            )
        )
        if existing is None:
            db.add(
                OauthAccount(
                    id=new_id("oauth"),
                    user_id=user.id,
                    provider=provider,
                    provider_subject=provider_subject,
                    created_at=now,
                )
            )
    if created:
        await db.flush()
    return user
