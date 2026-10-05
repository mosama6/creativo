from typing import Protocol

from creativo_api.errors import AppError
from creativo_contracts.generations import CreateGenerationRequest

ASPECTS = {
    "1:1": (1, 1),
    "3:2": (3, 2),
    "2:3": (2, 3),
    "16:9": (16, 9),
    "9:16": (9, 16),
}


def pixel_size(resolution: str, aspect: str) -> tuple[int, int]:
    long_edge = int(resolution)
    ratio_w, ratio_h = ASPECTS[aspect]
    if ratio_w >= ratio_h:
        width = long_edge
        height = round(long_edge * ratio_h / ratio_w)
    else:
        height = long_edge
        width = round(long_edge * ratio_w / ratio_h)
    return max(64, width // 8 * 8), max(64, height // 8 * 8)


class ModelAdapter(Protocol):
    model_id: str

    def validate(self, body: CreateGenerationRequest, capabilities: dict) -> None: ...

    def estimate_cost(self, body: CreateGenerationRequest, credit_cost: int) -> int: ...

    def build_parameters(self, body: CreateGenerationRequest) -> dict: ...


class FixtureImageAdapter:
    model_id = "fixture-image"

    def validate(self, body: CreateGenerationRequest, capabilities: dict) -> None:
        _validate_image_request(body, capabilities)

    def estimate_cost(self, body: CreateGenerationRequest, credit_cost: int) -> int:
        return _resolution_cost(body, credit_cost)

    def build_parameters(self, body: CreateGenerationRequest) -> dict:
        width, height = pixel_size(body.resolution or "1024", body.aspect_ratio or "1:1")
        return {
            "aspect_ratio": body.aspect_ratio,
            "resolution": body.resolution,
            "width": width,
            "height": height,
            "fixture_behavior": body.fixture_behavior or "success",
        }


class FluxImageAdapter:
    """FLUX.1 schnell. Guidance stays at 0 and the step count stays at 4."""

    model_id = "flux"

    def validate(self, body: CreateGenerationRequest, capabilities: dict) -> None:
        _validate_image_request(body, capabilities)

    def estimate_cost(self, body: CreateGenerationRequest, credit_cost: int) -> int:
        return _resolution_cost(body, credit_cost)

    def build_parameters(self, body: CreateGenerationRequest) -> dict:
        width, height = pixel_size(body.resolution or "512", body.aspect_ratio or "1:1")
        return {
            "aspect_ratio": body.aspect_ratio,
            "resolution": body.resolution,
            "width": max(64, width // 16 * 16),
            "height": max(64, height // 16 * 16),
            "num_inference_steps": 4,
            "guidance_scale": 0.0,
            "max_sequence_length": 256,
            "strength": 0.75,
        }


def _resolution_cost(body: CreateGenerationRequest, credit_cost: int) -> int:
    multiplier = 2 if body.resolution == "1024" else 1
    return credit_cost * multiplier


def _validate_image_request(body: CreateGenerationRequest, capabilities: dict) -> None:
    _require_mode(body, capabilities)
    if body.aspect_ratio not in capabilities.get("aspect_ratios", []):
        raise AppError("invalid_parameters", "Choose a supported aspect ratio.", 422)
    if body.resolution not in capabilities.get("resolutions", []):
        raise AppError("invalid_parameters", "Choose a supported resolution.", 422)
    if body.duration is not None:
        raise AppError("invalid_parameters", "Duration applies to video models.", 422)
    limit = int(capabilities.get("max_reference_images", 0))
    refs = body.reference_images
    if body.mode == "text_to_image" and refs:
        raise AppError("invalid_parameters", "Text to image does not take a reference.", 422)
    if body.mode == "image_to_image" and len(refs) != 1:
        raise AppError("invalid_parameters", "Image to image needs one reference image.", 422)
    if len(refs) > limit:
        raise AppError("invalid_parameters", "Too many reference images for this model.", 422)


def _require_mode(body: CreateGenerationRequest, capabilities: dict) -> None:
    flag = {
        "text_to_image": "text_to_image",
        "image_to_image": "image_to_image",
        "text_to_video": "text_to_video",
        "image_to_video": "image_to_video",
        "multi_image_to_video": "multi_image_to_video",
    }[body.mode]
    if not capabilities.get(flag, False):
        raise AppError("unsupported_mode", "This model does not support that mode.", 422)
    if body.type == "image" and body.mode not in {"text_to_image", "image_to_image"}:
        raise AppError("invalid_parameters", "Image generations cannot use a video mode.", 422)
    if body.type == "video" and body.mode in {"text_to_image", "image_to_image"}:
        raise AppError("invalid_parameters", "Video generations cannot use an image mode.", 422)


class QwenImageAdapter:
    """Qwen-Image-2512 with the 4-step Lightning LoRA. CFG 1, text to image only."""

    model_id = "qwen-image"

    def validate(self, body: CreateGenerationRequest, capabilities: dict) -> None:
        _validate_image_request(body, capabilities)

    def estimate_cost(self, body: CreateGenerationRequest, credit_cost: int) -> int:
        return credit_cost

    def build_parameters(self, body: CreateGenerationRequest) -> dict:
        width, height = pixel_size(body.resolution or "1024", body.aspect_ratio or "1:1")
        return {
            "aspect_ratio": body.aspect_ratio,
            "resolution": body.resolution,
            "width": max(64, width // 16 * 16),
            "height": max(64, height // 16 * 16),
            "num_inference_steps": 4,
            "true_cfg_scale": 1.0,
        }


ADAPTERS: dict[str, ModelAdapter] = {
    FixtureImageAdapter.model_id: FixtureImageAdapter(),
    FluxImageAdapter.model_id: FluxImageAdapter(),
    QwenImageAdapter.model_id: QwenImageAdapter(),
}


def adapter_for(model_id: str) -> ModelAdapter | None:
    return ADAPTERS.get(model_id)
