from typing import Any

from pydantic import BaseModel, Field

from creativo_contracts.enums import GenerationMode, GenerationType, WorkerStatus


class ImageInput(BaseModel):
    asset_id: str
    storage_key: str


class WorkerInputs(BaseModel):
    images: list[ImageInput] = Field(default_factory=list)


class GenerateRequest(BaseModel):
    job_id: str
    generation_id: str
    attempt: int = Field(ge=1)
    model: str
    model_version: str
    type: GenerationType
    mode: GenerationMode
    prompt: str
    inputs: WorkerInputs
    parameters: dict[str, Any]


class GenerateBatchRequest(BaseModel):
    """Several independent prompts for the same model, run in one GPU pass."""

    jobs: list[GenerateRequest] = Field(min_length=1, max_length=8)


class WorkerOutput(BaseModel):
    type: str
    storage_key: str
    content_type: str
    byte_size: int


class WorkerUsage(BaseModel):
    gpu_seconds: float = 0


class WorkerJobView(BaseModel):
    job_id: str
    status: str
    retryable: bool = False
    error_code: str | None = None
    error_message: str | None = None
    output: WorkerOutput | None = None
    outputs: list[WorkerOutput] = Field(default_factory=list)
    usage: WorkerUsage | None = None


class WorkerHeartbeat(BaseModel):
    worker_id: str
    model: str
    model_version: str
    gpu: str
    vram_gb: int
    status: WorkerStatus
    active_jobs: int = 0
    max_concurrent_jobs: int = 1
    max_batch_size: int = 1
    queue_depth: int = Field(
        default=0,
        description="In-flight jobs. Workers do not pull their own queue.",
    )
    advertise_url: str
    hourly_micro_usd: int = 0
    last_error: str | None = None


class WorkerCapabilities(BaseModel):
    model: str
    model_version: str
    capabilities: dict[str, Any]
