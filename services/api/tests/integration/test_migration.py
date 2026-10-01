from alembic import command
from alembic.config import Config
from alembic.script import ScriptDirectory
from sqlalchemy import Engine, create_engine, inspect, text
from sqlalchemy.exc import IntegrityError

from tests.conftest import TEST_DB_URL
from tests.integration.conftest import API_DIR, TABLES, prepare_test_db

NEW_TABLES = set(TABLES) - {"model_calls", "model_cache"}


def _cfg() -> Config:
    cfg = Config(str(API_DIR / "alembic.ini"))
    cfg.set_main_option("script_location", str(API_DIR / "migrations"))
    cfg.set_main_option("sqlalchemy.url", TEST_DB_URL)
    return cfg


def test_migration_0002() -> None:
    """0002_adstudio goes down and up cleanly and enforces the C1 cap in the database."""
    cfg = _cfg()
    engine = create_engine(TEST_DB_URL)
    try:
        command.downgrade(cfg, "0001b_ledger_run_id")
        tables = set(inspect(engine).get_table_names())
        assert not (NEW_TABLES & tables) and "model_calls" in tables
        command.upgrade(cfg, "head")
        insp = inspect(engine)
        assert NEW_TABLES <= set(insp.get_table_names())
        fks = {fk["name"]: fk["referred_table"] for fk in insp.get_foreign_keys("model_calls")}
        assert fks.get("fk_model_calls_run_id_runs") == "runs"

        insert = text(
            "INSERT INTO images (id, sha256, source, mime, width, height, bytes) "
            "VALUES (gen_random_uuid(), :sha, :source, 'image/png', :w, :h, 1)"
        )
        with engine.begin() as conn:
            conn.execute(insert, {"sha": "a" * 64, "source": "upload", "w": 2048, "h": 1536})
            conn.execute(insert, {"sha": "b" * 64, "source": "generated", "w": 1024, "h": 819})
        for source in ("generated", "repair", "overlay"):
            try:
                with engine.begin() as conn:
                    conn.execute(insert, {"sha": "c" * 64, "source": source, "w": 1025, "h": 800})
            except IntegrityError as exc:
                assert "ck_images_generated_max_edge" in str(exc)
            else:
                raise AssertionError(f"oversize {source} image was accepted")
        with engine.begin() as conn:
            conn.execute(text("TRUNCATE images CASCADE"))
    finally:
        command.upgrade(cfg, "head")
        engine.dispose()


def test_migration_0004_composition() -> None:
    """0004_composition adds the composition columns and allows 'composition' checks; down and up
    are clean."""
    cfg = _cfg()
    engine = create_engine(TEST_DB_URL)
    try:
        command.downgrade(cfg, "0003_orchestrator")
        cols = {c["name"] for c in inspect(engine).get_columns("evaluations")}
        assert "composition_pass" not in cols
        command.upgrade(cfg, "head")
        insp = inspect(engine)
        assert "composition_pass" in {c["name"] for c in insp.get_columns("evaluations")}
        assert "composition_ok" in {c["name"] for c in insp.get_columns("labels")}
        checks = {c["name"]: c["sqltext"] for c in insp.get_check_constraints("evaluation_checks")}
        assert "composition" in str(checks["ck_evaluation_checks_dimension"])
    finally:
        command.upgrade(cfg, "head")
        engine.dispose()


_ORPHAN_CALL = text(
    "INSERT INTO model_calls (id, kind, operation, requested_model, fallback_used, cached, "
    "input_units, output_units, unit_type, latency_ms, est_cost_usd, status, meta, run_id) "
    "VALUES (gen_random_uuid(), 'text', 'test.orphan', 'test:model', false, false, 0, 0, "
    "'tokens', 0, 0, 'ok', '{}'::jsonb, gen_random_uuid())"
)


def _revision(engine: Engine) -> str:
    with engine.connect() as conn:
        return str(conn.execute(text("SELECT version_num FROM alembic_version")).scalar_one())


def test_interrupted_migration_test_recovers() -> None:
    """Regression: a migration test interrupted at 0001b (runs dropped, ledger rows left with
    run_ids pointing nowhere) used to block every later session, because 0002 could not re-add
    the model_calls.run_id FK. Both 0002's upgrade and the session setup now recover."""
    cfg = _cfg()
    engine = create_engine(TEST_DB_URL)
    try:
        # 1. 0002's upgrade clears orphan run_ids before adding the FK.
        command.downgrade(cfg, "0001b_ledger_run_id")
        with engine.begin() as conn:
            for _ in range(3):
                conn.execute(_ORPHAN_CALL)
        command.upgrade(cfg, "head")
        fks = {fk["name"] for fk in inspect(engine).get_foreign_keys("model_calls")}
        assert "fk_model_calls_run_id_runs" in fks
        with engine.connect() as conn:
            left = conn.execute(text("SELECT count(*) FROM model_calls WHERE run_id IS NOT NULL"))
            assert left.scalar_one() == 0

        # 2. The session setup (what the next pytest run does first) recovers from the same
        # interrupted state and leaves an empty database at head.
        command.downgrade(cfg, "0001b_ledger_run_id")
        with engine.begin() as conn:
            conn.execute(_ORPHAN_CALL)
        prepare_test_db()
        assert _revision(engine) == ScriptDirectory.from_config(cfg).get_current_head()
        with engine.connect() as conn:
            assert conn.execute(text("SELECT count(*) FROM model_calls")).scalar_one() == 0
    finally:
        command.upgrade(cfg, "head")
        engine.dispose()
