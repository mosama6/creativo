from redis.asyncio import Redis

from creativo_api.errors import AppError
from creativo_common.keys import enqueued_key, queue_key, rate_key


async def publish_job(redis: Redis, model_id: str, job_id: str) -> None:
    await redis.lpush(queue_key(model_id), job_id)
    await redis.set(enqueued_key(job_id), "1", ex=86_400)


async def enforce_rate(
    redis: Redis, scope: str, subject: str, limit: int, window_seconds: int
) -> None:
    key = rate_key(scope, subject)
    count = await redis.incr(key)
    if count == 1:
        await redis.expire(key, window_seconds)
    if int(count) > limit:
        raise AppError("rate_limited", "Too many requests. Wait a moment and try again.", 429)
