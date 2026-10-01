import json
from datetime import UTC, datetime
from pathlib import Path

from evals.report import build_rows, meets, write_report


def test_meets() -> None:
    assert meets(0.9, (">=", 0.8)) is True
    assert meets(12, ("<=", 10)) is False
    assert meets("skipped", (">=", 1)) is None
    assert meets(None, (">=", 1)) is None


def test_rows_compare_to_baseline() -> None:
    rows = build_rows({"a.acc": 0.9}, {"a.acc": (">=", 0.8), "a.miss": ("<", 1)}, {"a.acc": 0.7})
    by = {r["metric"]: r for r in rows}
    assert by["a.acc"]["baseline"] == 0.7 and by["a.acc"]["pass"] is True
    assert by["a.miss"]["now"] is None and by["a.miss"]["pass"] is None


def test_baseline_only_from_numeric_run(tmp_path: Path) -> None:
    write_report(
        {"s.status": "skipped"}, {}, reports_dir=tmp_path, now=datetime(2026, 1, 1, tzinfo=UTC)
    )
    assert not (tmp_path / "baseline.json").exists()
    write_report(
        {"s.acc": 0.5},
        {"s.acc": (">=", 0.8)},
        reports_dir=tmp_path,
        now=datetime(2026, 1, 2, tzinfo=UTC),
    )
    assert json.loads((tmp_path / "baseline.json").read_text()) == {"s.acc": 0.5}
    md = write_report(
        {"s.acc": 0.9},
        {"s.acc": (">=", 0.8)},
        reports_dir=tmp_path,
        now=datetime(2026, 1, 3, tzinfo=UTC),
    )
    assert "| s.acc | >= 0.8 | 0.5 | 0.9 | pass |" in md.read_text()
    write_report(
        {"s.acc": 0.95},
        {},
        reports_dir=tmp_path,
        reset_baseline=True,
        now=datetime(2026, 1, 4, tzinfo=UTC),
    )
    assert json.loads((tmp_path / "baseline.json").read_text()) == {"s.acc": 0.95}
