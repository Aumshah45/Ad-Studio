"""The v1 reason relabel sheet (analysis R1b): it preloads exactly the product/context fails, keeps
every other row unchanged for the download, and carries no evaluator output. No network."""

import json
import re
from pathlib import Path

from backend.golden.dataset import (
    GOLDEN_DIR,
    REASON_CODES,
    GoldenPaths,
    golden_out,
    parse_reasons,
    read_labels,
)
from backend.golden.sheet import reason_targets, render_reasons_sheet

V1 = GoldenPaths(source=GOLDEN_DIR, out=golden_out("v1"))


def _v1_with_committed_labels(tmp_path: Path) -> GoldenPaths:
    """A v1 out dir with the labels as they were before the reason pass (no `reason:` notes),
    so the test does not depend on whether the human has saved reasons yet."""
    out = tmp_path / "v1"
    out.mkdir()
    (out / "outputs").symlink_to(V1.outputs)
    (out / "cache").symlink_to(V1.out / "cache")
    text = V1.labels.read_text(encoding="utf-8")
    text = re.sub(r"(; )?reason:\w+=\w+(; (product|context): [^,\"]*)?", "", text)
    (out / "labels.csv").write_text(text, encoding="utf-8")
    return GoldenPaths(source=GOLDEN_DIR, out=out)


def _const(page: str, name: str) -> object:
    match = re.search(rf"const {name} = (.*?);\n", page, re.S)
    assert match, name
    return json.loads(match.group(1))


def test_reason_sheet_preloads_exactly_the_product_and_context_fails(tmp_path: Path) -> None:
    paths = _v1_with_committed_labels(tmp_path)
    labels = read_labels(paths.labels)
    expected = {
        f"{sha}|{s}"
        for (sha, s), row in labels.items()
        if row.product is False or row.context is False
    }
    failed_dims = sum((r.product is False) + (r.context is False) for r in labels.values())
    # 14 images / 19 dimension fails before the reason pass; 5 context fails after it (d779115).
    assert expected and failed_dims >= len(expected)
    assert set(reason_targets(labels)) == expected

    page = render_reasons_sheet(paths, embed=False)
    preload = _const(page, "PRELOAD")
    unchanged = _const(page, "UNCHANGED")
    order = _const(page, "ORDER")
    assert isinstance(preload, dict) and isinstance(unchanged, dict) and isinstance(order, list)
    assert set(preload) == expected
    assert set(unchanged) | set(preload) == set(order) and len(order) == 40
    assert not set(unchanged) & set(preload)
    # current labels are preloaded as they are
    for key, row in preload.items():
        sha, image_set = key.split("|")
        label = labels[(sha, image_set)]
        assert row["product"] == ("fail" if label.product is False else "pass")
        assert row["context"] == ("fail" if label.context is False else "pass")
        assert row["notes"] == label.notes
    # one card per row, one required reason select per failed dimension, the reference beside it
    assert page.count('class="card"') == len(expected) == page.count('class="ref"')
    assert page.count("<select ") == failed_dims == page.count(" required>")
    for code in REASON_CODES:
        assert f'<option value="{code}">' in page
    assert _const(page, "COMP_REASONS") == ["pasted", "scale"]


def test_reason_sheet_leaks_no_evaluator_verdicts(tmp_path: Path) -> None:
    page = render_reasons_sheet(_v1_with_committed_labels(tmp_path), embed=False)
    for leak in (
        "ocr_cer",
        "color_delta_e",
        "delta_e",
        "needs_review",
        "pipeline_verdict",
        "verdicts.json",
        "comp.realistic",
        "ctx.",
        "prod.",
        "judge",
        "evaluator_pass",
        "repairs",
    ):
        assert leak not in page, leak


def test_parse_reasons() -> None:
    notes = "[technical fail→pass: x]; reason:product=scale; reason:context=people; context: hand"
    assert parse_reasons(notes) == {"product": "scale", "context": "people"}
    assert parse_reasons("reason:product=bogus") == {}
    assert parse_reasons("") == {}
