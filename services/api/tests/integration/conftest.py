import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, inspect, text

from tests.conftest import TEST_DB_URL

API_DIR = __import__("pathlib").Path(__file__).resolve().parents[2]
TABLES = (
    "model_calls",
    "model_cache",
    "images",
    "products",
    "briefs",
    "runs",
    "run_events",
    "candidates",
    "evaluations",
    "evaluation_checks",
    "labels",
    "planted_failures",
    "eval_reports",
    "run_decisions",
)


def alembic_config(url: str = TEST_DB_URL) -> Config:
    cfg = Config(str(API_DIR / "alembic.ini"))
    cfg.set_main_option("script_location", str(API_DIR / "migrations"))
    cfg.set_main_option("sqlalchemy.url", url)
    return cfg


def truncate_existing(url: str = TEST_DB_URL) -> list[str]:
    """Empty every app table that exists, whatever revision the schema is at. The test database
    holds only test data, so leftovers (e.g. ledger rows whose runs a half-finished migration test
    dropped) are cleared instead of blocking the next upgrade."""
    engine = create_engine(url)
    try:
        present = [t for t in TABLES if t in set(inspect(engine).get_table_names())]
        if present:
            with engine.begin() as conn:
                conn.execute(text(f"TRUNCATE {', '.join(present)} CASCADE"))
        return present
    finally:
        engine.dispose()


def prepare_test_db(url: str = TEST_DB_URL) -> None:
    """Self-healing session setup: clear stale rows, migrate to head, clear again."""
    truncate_existing(url)
    command.upgrade(alembic_config(url), "head")
    truncate_existing(url)


@pytest.fixture(scope="session", autouse=True)
def migrated_db() -> None:
    """Synchronous on purpose: env.py calls asyncio.run."""
    prepare_test_db()
