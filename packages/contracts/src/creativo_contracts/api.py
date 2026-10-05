from datetime import datetime
from typing import Any

from pydantic import BaseModel


class ModelVersionView(BaseModel):
    id: str
    version: str
    status: str
    credit_cost: int
    pricing_status: str
    required_vram_gb: int
    supported_gpus: list[str]
    timeout_seconds: int
    max_attempts: int
    capabilities: dict[str, Any]
    container_image: str


class ModelView(BaseModel):
    id: str
    display_name: str
    provider: str
    modality: str
    enabled: bool
    status: str
    summary: str
    version: ModelVersionView | None


class AuthProviders(BaseModel):
    google: bool
    dev_login: bool


class UserView(BaseModel):
    id: str
    email: str
    name: str
    avatar_url: str | None
    created_at: datetime


class AssetView(BaseModel):
    id: str
    content_type: str
    byte_size: int
    width: int | None
    height: int | None
    created_at: datetime
