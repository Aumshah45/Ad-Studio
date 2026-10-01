"""Structure checks for the committed golden dataset (data/golden)."""

import re
import unicodedata
from pathlib import Path
from typing import Any

import yaml

GOLDEN = Path(__file__).resolve().parents[4] / "data" / "golden"


def _briefs() -> dict[str, Any]:
    return yaml.safe_load((GOLDEN / "briefs.yaml").read_text(encoding="utf-8"))


def test_golden_briefs_schema() -> None:
    data = _briefs()
    products: dict[str, Any] = data["products"]
    briefs: list[dict[str, Any]] = data["briefs"]

    assert len(products) == 5
    assert len(briefs) == 20
    assert [b["id"] for b in briefs] == [f"B{i:02d}" for i in range(1, 21)]
    for pid, product in products.items():
        assert (GOLDEN / product["file"]).is_file(), pid
        assert sum(b["product"] == pid for b in briefs) == 4, pid
    for b in briefs:
        assert re.fullmatch(r"[A-Z]{2}", str(b["geography"])), b["id"]
        assert 1 <= len(b["season"]) <= 40, b["id"]
        text = b["required_text"]
        assert text == unicodedata.normalize("NFC", text), b["id"]
        assert 1 <= len(text) <= 80, b["id"]


def test_sources_cover_every_product() -> None:
    sources = (GOLDEN / "SOURCES.md").read_text(encoding="utf-8")
    for pid, product in _briefs()["products"].items():
        row = next((line for line in sources.splitlines() if line.startswith(f"| {pid} |")), None)
        assert row is not None, pid
        assert f"`{product['file']}`" in row
        assert re.search(r"CC0|CC BY|Public domain", row), pid
