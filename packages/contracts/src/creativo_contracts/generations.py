from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field

from creativo_contracts.enums import (
    FixtureBehavior,
    GenerationMode,
    GenerationStatus,
    GenerationType,
)


class CreateGenerationRequest(BaseModel):
    type: GenerationType
    mode: GenerationMode
    model: str = Field(min_length=1, max_length=64)
    prompt: str = Field(min_length=1, max_length=4000)
    reference_images: list[str] = Field(default_factory=list, max_length=9)
    aspect_ratio: str | None = None
    resolution: str | None = None
    duration: int | None = Field(default=None, ge=1, le=30)
    fixture_behavior: FixtureBehavior | None = None


class GenerationOutputView(BaseModel):
    index: int = 0
    content_type: str
    byte_size: int
    gpu_millis: int


class GenerationView(BaseModel):
    id: str
    status: GenerationStatus
    type: GenerationType
    mode: GenerationMode
    model: str
    prompt: str
    prompt_final: str | None
    parameters: dict[str, Any]
    credit_price: int
    failure_code: str | None
    failure_message: str | None
    output: GenerationOutputView | None
    outputs: list[GenerationOutputView] = Field(default_factory=list)
    created_at: datetime
    updated_at: datetime
    completed_at: datetime | None


class GenerationPage(BaseModel):
    items: list[GenerationView]
    next_cursor: str | None
