import math

from creativo_contracts.worker import WorkerHeartbeat


def pack_limit(worker: WorkerHeartbeat) -> int:
    """How many different prompts this GPU can run in one pass."""
    return max(1, min(int(worker.max_batch_size), 8))


def choose_worker(workers: list[WorkerHeartbeat], model_id: str) -> WorkerHeartbeat | None:
    eligible = [
        worker
        for worker in workers
        if worker.model == model_id
        and worker.status == "READY"
        and worker.active_jobs < max(1, worker.max_concurrent_jobs)
    ]
    if not eligible:
        return None
    eligible.sort(
        key=lambda worker: (worker.active_jobs, worker.hourly_micro_usd, worker.worker_id)
    )
    return eligible[0]


def desired_replicas(
    *,
    queue_depth: int,
    in_flight: int,
    minimum: int,
    maximum: int,
    slots_per_worker: int = 1,
) -> int:
    if queue_depth <= 0 and in_flight <= 0:
        return max(0, minimum)
    needed = in_flight + math.ceil(queue_depth / max(1, slots_per_worker))
    return min(maximum, max(minimum, needed))


def step_towards(current: int, target: int) -> int:
    """Grow by doubling and shrink by one so a burst cannot rent a full pool at once."""
    if target == current:
        return current
    if target > current:
        grown = 1 if current <= 0 else current * 2
        return min(target, grown)
    return max(target, current - 1)
