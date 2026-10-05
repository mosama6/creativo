from pathlib import Path

from alembic import command
from alembic.config import Config

INI = Path(__file__).resolve().parents[2] / "alembic.ini"


def upgrade() -> None:
    cfg = Config(str(INI))
    command.upgrade(cfg, "head")
