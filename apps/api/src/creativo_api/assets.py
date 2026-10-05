import hashlib
from io import BytesIO

from PIL import Image, UnidentifiedImageError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from creativo_api.errors import AppError
from creativo_common.ids import new_id
from creativo_common.settings import PlatformSettings
from creativo_common.storage import FileStorage
from creativo_common.timeutil import utcnow
from creativo_contracts.api import AssetView
from creativo_db.models import Asset, User

MAX_EDGE = 4096


def _view(asset: Asset) -> AssetView:
    return AssetView(
        id=asset.id,
        content_type=asset.content_type,
        byte_size=asset.byte_size,
        width=asset.width,
        height=asset.height,
        created_at=asset.created_at,
    )


async def store_upload(
    db: AsyncSession,
    storage: FileStorage,
    settings: PlatformSettings,
    user: User,
    filename: str,
    data: bytes,
) -> AssetView:
    if not data:
        raise AppError("invalid_upload", "The upload was empty.", 400)
    if len(data) > settings.upload_max_bytes:
        raise AppError("invalid_upload", "That image is too large.", 413)
    try:
        image = Image.open(BytesIO(data))
        image.load()
    except (UnidentifiedImageError, OSError) as exc:
        raise AppError("invalid_upload", "Upload a PNG, JPEG, or WebP image.", 400) from exc
    if max(image.size) > MAX_EDGE:
        raise AppError("invalid_upload", "That image is larger than 4096 pixels on a side.", 400)
    image = image.convert("RGB")
    buffer = BytesIO()
    image.save(buffer, format="PNG", optimize=True)
    encoded = buffer.getvalue()
    asset_id = new_id("ast")
    key = f"assets/{user.id}/{asset_id}.png"
    storage.put(key, encoded)
    now = utcnow()
    asset = Asset(
        id=asset_id,
        user_id=user.id,
        storage_key=key,
        content_type="image/png",
        byte_size=len(encoded),
        width=image.width,
        height=image.height,
        sha256=hashlib.sha256(encoded).hexdigest(),
        created_at=now,
    )
    db.add(asset)
    await db.flush()
    return _view(asset)


async def list_assets(db: AsyncSession, user: User) -> list[AssetView]:
    rows = await db.scalars(
        select(Asset)
        .where(Asset.user_id == user.id, Asset.deleted_at.is_(None))
        .order_by(Asset.created_at.desc())
        .limit(60)
    )
    return [_view(row) for row in rows]


async def get_asset(db: AsyncSession, user: User, asset_id: str) -> Asset:
    asset = await db.get(Asset, asset_id)
    if asset is None or asset.user_id != user.id or asset.deleted_at is not None:
        raise AppError("asset_not_found", "Asset not found.", 404)
    return asset


async def delete_asset(db: AsyncSession, user: User, asset_id: str) -> None:
    asset = await get_asset(db, user, asset_id)
    asset.deleted_at = utcnow()
    await db.flush()
