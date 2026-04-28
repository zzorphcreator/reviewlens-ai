import os
import asyncio
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config

if test_database_url := os.environ.get("TEST_DATABASE_URL"):
    os.environ["DATABASE_URL"] = test_database_url
os.environ.setdefault("UPLOAD_DIR", "test_uploads")

_migrations_applied = False


@pytest.fixture(autouse=True)
def apply_migrations(request: pytest.FixtureRequest):
    global _migrations_applied
    if request.node.get_closest_marker("db") is None:
        yield
        return

    if not _migrations_applied:
        config = Config(str(Path(__file__).resolve().parents[1] / "alembic.ini"))
        command.upgrade(config, "head")
        _migrations_applied = True

    yield

    from backend.storage.database import engine

    asyncio.run(engine.dispose())
