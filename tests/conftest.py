import os
import sys

import pytest

# Ensure repository root is on PYTHONPATH
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

import pkg.models  # noqa: F401 - Register all SQLAlchemy models
from pkg.database import Base, engine


@pytest.fixture(scope="session", autouse=True)
async def init_test_db():
    """Initializes the database schema before test runs in CI or fresh environments."""
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield
