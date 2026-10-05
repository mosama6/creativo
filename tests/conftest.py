import os
from pathlib import Path

os.environ["APP_ENV"] = "test"
os.environ["DATABASE_URL"] = "postgresql+asyncpg://creativo:creativo@127.0.0.1:5433/creativo_test"
os.environ["REDIS_URL"] = "redis://127.0.0.1:6379/1"
os.environ["STORAGE_DIR"] = str(Path(__file__).resolve().parents[1] / "data" / "test-objects")
os.environ["RETRY_DELAY_SECONDS"] = "0.05"
os.environ["BATCH_FILL_SECONDS"] = "0"
os.environ["WORKER_ADVERTISE_URL"] = "http://127.0.0.1:8101"
os.environ["WORKER_ID"] = "worker-test"
os.environ["WORKER_MODEL_ID"] = "fixture-image"
os.environ["ORCHESTRATOR_ID"] = "orchestrator-test"
os.environ["DEV_STARTING_CREDITS"] = "40"

from creativo_common.settings import get_settings

get_settings.cache_clear()

pytest_plugins = ["tests.fixtures"]
