from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from creativo_common.timeutil import utcnow
from creativo_db.catalog import CATALOG
from creativo_db.models import CatalogModel, ModelVersion


async def seed_catalog(session: AsyncSession) -> None:
    now = utcnow()
    for spec in CATALOG:
        model = await session.get(CatalogModel, spec["id"])
        if model is None:
            model = CatalogModel(
                id=spec["id"],
                display_name=spec["display_name"],
                provider=spec["provider"],
                modality=spec["modality"],
                enabled=spec["enabled"],
                status=spec["status"],
                summary=spec["summary"],
                created_at=now,
            )
            session.add(model)
        else:
            model.display_name = spec["display_name"]
            model.provider = spec["provider"]
            model.modality = spec["modality"]
            model.enabled = spec["enabled"]
            model.status = spec["status"]
            model.summary = spec["summary"]

        version_id = f"{spec['id']}:{spec['version']}"
        version = await session.get(ModelVersion, version_id)
        fields = {
            "container_image": spec["container_image"],
            "weights_version": spec["weights_version"],
            "required_vram_gb": spec["required_vram_gb"],
            "supported_gpus": spec["supported_gpus"],
            "credit_cost": spec["credit_cost"],
            "pricing_status": spec["pricing_status"],
            "timeout_seconds": spec["timeout_seconds"],
            "max_attempts": spec["max_attempts"],
            "capabilities": spec["capabilities"],
            "status": "active" if spec["enabled"] else "planned",
        }
        if version is None:
            session.add(
                ModelVersion(
                    id=version_id,
                    model_id=spec["id"],
                    version=spec["version"],
                    created_at=now,
                    **fields,
                )
            )
        else:
            for key, value in fields.items():
                setattr(version, key, value)
    await session.flush()


async def enabled_model_ids(session: AsyncSession) -> list[str]:
    rows = await session.scalars(select(CatalogModel.id).where(CatalogModel.enabled.is_(True)))
    return list(rows)
