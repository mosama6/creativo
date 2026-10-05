import asyncio
import socket
from urllib.parse import urlparse

import asyncpg
import pytest
import uvicorn
from httpx import ASGITransport, AsyncClient
from redis.asyncio import Redis
from sqlalchemy import text

from creativo_api.app import create_app
from creativo_common.settings import get_settings
from creativo_common.storage import FileStorage
from creativo_db.migrate import upgrade
from creativo_db.seed import seed_catalog
from creativo_db.session import create_engine, create_session_factory
from creativo_orchestrator.engine import Dispatcher
from creativo_worker.main import create_app as create_worker_app
from creativo_worker.main import publish_heartbeat


def _postgres_endpoint() -> tuple[str, int]:
    parsed = urlparse(get_settings().database_url.replace("+asyncpg", ""))
    return parsed.hostname or "127.0.0.1", parsed.port or 5433


def _ports_open() -> bool:
    try:
        host, port = _postgres_endpoint()
        for target in ((host, port), ("127.0.0.1", 6379)):
            with socket.create_connection(target, 0.4):
                pass
        return True
    except OSError:
        return False


def _ensure_database() -> None:
    host, port = _postgres_endpoint()

    async def create() -> None:
        conn = await asyncpg.connect(
            user="creativo",
            password="creativo",
            database="postgres",
            host=host,
            port=port,
        )
        try:
            await conn.execute("CREATE DATABASE creativo_test")
        except asyncpg.DuplicateDatabaseError:
            pass
        finally:
            await conn.close()

    asyncio.run(create())
    upgrade()


@pytest.fixture
def database_url():
    if not _ports_open():
        pytest.skip("Postgres and Redis are not running")
    _ensure_database()
    return get_settings().database_url


@pytest.fixture
async def db_factory(database_url):
    engine = create_engine(database_url)
    factory = create_session_factory(engine)
    async with engine.begin() as conn:
        await conn.execute(
            text(
                """
                TRUNCATE TABLE
                  worker_records, idempotency_keys, safety_events, credit_transactions,
                  credit_accounts, job_attempts, jobs, generation_outputs, generation_inputs,
                  generations, assets, model_versions, models, sessions, oauth_accounts, users
                RESTART IDENTITY CASCADE
                """
            )
        )
    async with factory() as session:
        await seed_catalog(session)
        await session.commit()
    yield factory
    await engine.dispose()


@pytest.fixture
async def redis(database_url):
    client = Redis.from_url(get_settings().redis_url, decode_responses=True)
    await client.flushdb()
    yield client
    await client.aclose()


@pytest.fixture
async def worker(redis):
    application = create_worker_app()
    config = uvicorn.Config(application, host="127.0.0.1", port=8101, log_level="warning")
    server = uvicorn.Server(config)
    server.install_signal_handlers = lambda: None
    task = asyncio.create_task(server.serve())
    for _ in range(50):
        if getattr(application.state, "ready", False):
            break
        await asyncio.sleep(0.05)
    await publish_heartbeat(application)
    yield application
    server.should_exit = True
    await task


@pytest.fixture
async def dispatcher(db_factory, redis, worker):
    await redis.flushdb()
    await publish_heartbeat(worker)
    instance = Dispatcher(
        settings=get_settings(),
        redis=redis,
        sessions=db_factory,
        storage=FileStorage(get_settings().storage_dir),
    )
    yield instance
    await instance.aclose()


@pytest.fixture
async def application(dispatcher):
    application = create_app()
    async with application.router.lifespan_context(application):
        yield application


@pytest.fixture
async def api(application):
    transport = ASGITransport(app=application)

    def open_client():
        return AsyncClient(transport=transport, base_url="http://test")

    yield open_client
