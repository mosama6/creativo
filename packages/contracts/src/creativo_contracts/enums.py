from typing import Literal

GenerationStatus = Literal[
    "rejected",
    "queued",
    "running",
    "completed",
    "failed",
    "cancelled",
]

JobStatus = Literal["queued", "running", "completed", "failed", "cancelled"]

AttemptStatus = Literal["started", "succeeded", "retryable_failure", "permanent_failure"]

GenerationType = Literal["image", "video"]

GenerationMode = Literal[
    "text_to_image",
    "image_to_image",
    "text_to_video",
    "image_to_video",
    "multi_image_to_video",
]

FixtureBehavior = Literal["success", "retryable", "permanent", "hang"]

CreditTransactionType = Literal[
    "purchase",
    "reservation",
    "consumption",
    "refund",
    "bonus",
    "admin_adjustment",
]

WorkerStatus = Literal[
    "STARTING",
    "LOADING_MODEL",
    "READY",
    "BUSY",
    "DRAINING",
    "UNHEALTHY",
    "STOPPING",
    "STOPPED",
]

TERMINAL_GENERATION_STATUSES = frozenset({"rejected", "completed", "failed", "cancelled"})

NON_RETRYABLE_ERRORS = frozenset(
    {"invalid_parameters", "unsupported_mode", "content_rejected", "invalid_output"}
)

RETRYABLE_ERRORS = frozenset(
    {
        "worker_timeout",
        "worker_unreachable",
        "worker_busy",
        "cuda_oom",
        "model_crash",
        "storage_failed",
        "transient",
    }
)


def is_retryable(error_code: str, worker_says_retryable: bool) -> bool:
    """Unknown codes fail permanently so a worker cannot loop a poison job."""
    if error_code in NON_RETRYABLE_ERRORS:
        return False
    if error_code in RETRYABLE_ERRORS:
        return worker_says_retryable or error_code in {
            "worker_timeout",
            "worker_unreachable",
            "worker_busy",
            "storage_failed",
        }
    return False
