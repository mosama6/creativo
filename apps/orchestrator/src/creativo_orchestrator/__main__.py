import asyncio
import logging
import time

import uvicorn
from fastapi import FastAPI
from redis.asyncio import Redis

from creativo_common.logging import configure_logging
from creativo_common.settings import get_settings
from creativo_common.storage import FileStorage
from creativo_db.session import create_engine, create_session_factory
from creativo_orchestrator.engine import Dispatcher
from creativo_orchestrator.scheduler import desired_replicas, step_towards

logger = logging.getLogger(__name__)


def create_app(dispatcher: Dispatcher) -> FastAPI:
    app = FastAPI(title="Creativo orchestrator")

    @app.get("/health")
    async def health() -> dict[str, str]:
        return {"status": "ok", "service": "orchestrator"}

    app.state.dispatcher = dispatcher
    return app


async def _log_capacity(dispatcher: Dispatcher) -> None:
    for model_id in await dispatcher._models():
        depth = int(await dispatcher.redis.llen(dispatcher_queue(model_id)))
        workers = await dispatcher._workers(model_id)
        current = len(workers)
        in_flight = sum(worker.active_jobs for worker in workers)
        target = desired_replicas(queue_depth=depth, in_flight=in_flight, minimum=0, maximum=4)
        nxt = step_towards(current, target)
        logger.info(
            "capacity plan model=%s current=%s target=%s next=%s depth=%s",
            model_id,
            current,
            target,
            nxt,
            depth,
            extra={"model_id": model_id},
        )


def dispatcher_queue(model_id: str) -> str:
    from creativo_common.keys import queue_key

    return queue_key(model_id)


async def run() -> None:
    configure_logging()
    settings = get_settings()
    settings.assert_production_safe()
    engine = create_engine(settings.database_url)
    sessions = create_session_factory(engine)
    redis = Redis.from_url(settings.redis_url, decode_responses=True)
    dispatcher = Dispatcher(
        settings=settings,
        redis=redis,
        sessions=sessions,
        storage=FileStorage(settings.storage_dir),
    )
    stop = asyncio.Event()

    async def loop() -> None:
        next_capacity = 0.0
        while not stop.is_set():
            try:
                worked = await dispatcher.dispatch_once()
                await dispatcher.reconcile()
                now = time.monotonic()
                if now >= next_capacity:
                    await _log_capacity(dispatcher)
                    next_capacity = now + 30
                if not worked:
                    await asyncio.sleep(0.4)
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("orchestrator tick failed")
                await asyncio.sleep(1)

    application = create_app(dispatcher)
    config = uvicorn.Config(
        application,
        host="0.0.0.0",
        port=settings.orchestrator_port,
        log_level="info",
    )
    server = uvicorn.Server(config)
    task = asyncio.create_task(loop())
    try:
        await server.serve()
    finally:
        stop.set()
        task.cancel()
        await dispatcher.aclose()
        await redis.aclose()
        await engine.dispose()


def main() -> None:
    asyncio.run(run())


if __name__ == "__main__":
    main()
