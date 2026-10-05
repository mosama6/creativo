def queue_key(model_id: str) -> str:
    return f"queue:{model_id}"


def processing_key(model_id: str) -> str:
    return f"processing:{model_id}"


def worker_key(worker_id: str) -> str:
    return f"worker:{worker_id}"


def workers_model_key(model_id: str) -> str:
    return f"workers:model:{model_id}"


def lease_key(job_id: str) -> str:
    return f"lease:{job_id}"


def enqueued_key(job_id: str) -> str:
    return f"enqueued:{job_id}"


def rate_key(scope: str, subject: str) -> str:
    return f"rl:{scope}:{subject}"


def oauth_state_key(state: str) -> str:
    return f"oauth:state:{state}"
