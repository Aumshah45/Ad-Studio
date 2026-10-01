"""ADR-007 golden tooling: versioned dirs, rubric-v2 labels, the v1 composition sheet and the
v1 -> v2 comparison in the eval report. No network."""

import json
import re
from pathlib import Path

from backend.golden.dataset import (
    GOLDEN_DIR,
    LABEL_COLUMNS,
    GoldenPaths,
    LabelRow,
    golden_out,
    labels_csv,
    read_labels,
    write_labels,
)
from backend.golden.sheet import COMPOSITION_SHEET, render_sheet, sheet_path
from evals.golden import EvalOutcome, attach_comparison, repo_relative
from evals.report import read_baseline, render_golden_markdown, write_golden_report
from evals.suites import evaluator_meta
from evals.suites.version_compare import compare, render_markdown
from tests.unit.test_eval_report import ALL_PASS, _report, fails, rec

V1 = GoldenPaths(source=GOLDEN_DIR, out=golden_out("v1"))
# The user's original rubric-1 labels, preserved when rubric 2 added the composition column
# (data/golden/v1/labels.csv now holds the rubric-2 labels, composition filled in).
V1_RUBRIC1 = V1.out / "labels_rubric1.csv"


def _v1_with_rubric1_labels(tmp_path: Path) -> GoldenPaths:
    """A v1 out dir whose labels.csv is the rubric-1 file (composition not labelled yet)."""
    out = tmp_path / "v1"
    out.mkdir()
    (out / "outputs").symlink_to(V1.outputs)
    (out / "cache").symlink_to(V1.out / "cache")
    (out / "labels.csv").write_bytes(V1_RUBRIC1.read_bytes())
    return GoldenPaths(source=GOLDEN_DIR, out=out)


def test_labels_round_trip_with_composition(tmp_path: Path) -> None:
    rows = [
        LabelRow(
            image_sha="a" * 64,
            set="final",
            brief_id="B17",
            technical=True,
            text=True,
            product=True,
            context=True,
            composition=False,
            notes="tube towers over the chair",
        )
    ]
    path = tmp_path / "labels.csv"
    write_labels(path, rows)
    assert path.read_text().splitlines()[0] == ",".join(LABEL_COLUMNS)
    back = read_labels(path)[("a" * 64, "final")]
    assert back.composition is False and back.rubric_version == "2" and not back.all_pass
    # A rubric-1 file (no composition column) still reads: composition unlabelled.
    old = read_labels(V1_RUBRIC1)
    assert len(old) == 40 and all(r.composition is None for r in old.values())
    assert all(r.complete for r in old.values())
    assert "composition" in labels_csv(list(old.values())).splitlines()[0]


def test_v1_composition_sheet_preloads_labels_and_opens_only_composition(tmp_path: Path) -> None:
    v1 = _v1_with_rubric1_labels(tmp_path)
    page = render_sheet(v1, embed=False, composition=True)
    assert page.count('class="card"') == 40
    assert "Golden v1 composition pass" in page
    assert "data/golden/v1/labels.csv" in page
    preload = json.loads(re.search(r"const PRELOAD = (\{.*?\});\n", page, re.S).group(1))  # type: ignore[union-attr]
    assert len(preload) == 40
    b10 = next(v for k, v in preload.items() if k.endswith("|final") and v["technical"] == "fail")
    assert b10["composition"] == "" and b10["context"] in ("pass", "fail")
    # text, product and context are read-only (40 cards x 3); technical and composition are open.
    assert page.count('<fieldset class="dim" disabled>') == 120
    assert page.count('<fieldset class="dim focus">') == 40
    assert f"const COLUMNS = {json.dumps(list(LABEL_COLUMNS))};" in page
    assert 'const RUBRIC = "2";' in page
    for leak in ("ocr_cer", "color_delta_e", "needs_review", "pipeline_verdict", "comp.realistic"):
        assert leak not in page  # blind to the evaluator
    assert sheet_path(v1, composition=True) == v1.out / COMPOSITION_SHEET
    blind = render_sheet(v1, embed=False)
    assert "const PRELOAD = {};" in blind and "disabled>" not in blind


def _versioned(version: str, human_comp: bool | None, judged_comp: bool) -> object:
    labels = {**ALL_PASS, "composition": human_comp}
    dims = {**ALL_PASS, "composition": judged_comp}
    items = [
        rec("B17-final", "final", labels, dims),
        rec("B13-final", "final", labels, {**ALL_PASS, "composition": True}),
        rec("B17-nat", "nat", fails("text"), fails("text")),
    ]
    data = _report(evaluator_meta.compute(items))
    return data.model_copy(
        update={"golden_version": version, "report_file": f"20260101T000000Z-{version}.md"}
    )


def test_comparison_reports_human_and_evaluator_rates_per_version(tmp_path: Path) -> None:
    v1 = _versioned("v1", None, False)  # rubric 1: composition not human-labelled
    v2 = _versioned("v2", True, True)
    comparison = compare([v2, v1])  # type: ignore[list-item]
    assert [v.version for v in comparison.versions] == ["v1", "v2"]
    s1, s2 = comparison.versions
    assert s1.human_pass_rates["final"]["composition"].n == 0
    assert s2.human_pass_rates["final"]["composition"].rate == 1.0
    assert s1.evaluator_pass_rates["final"]["composition"].rate == 0.5
    assert s2.evaluator_pass_rates["final"]["composition"].rate == 1.0
    assert s1.human_pass_rates["nat"]["text"].rate == 0.0
    md = "\n".join(render_markdown(comparison))
    assert "## Golden v1 → v2 (ADR-007)" in md
    assert "| E-final composition | 50% (1/2) | 100% (2/2) |" in md
    assert "Evaluator precision / recall" in md

    outcomes = []
    for data in (v1, v2):
        path = write_golden_report(data, reports_dir=tmp_path)
        outcomes.append(EvalOutcome(data, path))  # type: ignore[arg-type]
    assert (tmp_path / "baseline-v1.json").exists() and (tmp_path / "baseline-v2.json").exists()
    assert read_baseline(tmp_path, "v1") is not None
    attach_comparison(outcomes, reports_dir=tmp_path)
    newest = (tmp_path / "20260101T000000Z-v2.md").read_text()
    assert "## Golden v1 → v2 (ADR-007)" in newest and "Golden dataset **v2**" in newest
    assert "Golden v1 → v2" not in (tmp_path / "20260101T000000Z-v1.md").read_text()
    assert "Golden v1 → v2" in render_golden_markdown(outcomes[1].report)


def test_v3_reads_its_own_unseen_briefs_and_v1_v2_keep_the_shared_ones() -> None:
    from backend.golden.dataset import load_golden, split_of

    v3 = GoldenPaths(source=GOLDEN_DIR, out=golden_out("v3"))
    assert v3.briefs == GOLDEN_DIR / "v3" / "briefs.yaml"
    assert V1.briefs == GOLDEN_DIR / "briefs.yaml"
    golden = load_golden(v3)
    ids = [b.id for b in golden.briefs]
    assert len(ids) == 16 and len(set(ids)) == 16
    assert not set(ids) & {b.id for b in load_golden(V1).briefs}  # unseen: no shared brief ids
    assert {split_of(b.product) for b in golden.briefs} == {"heldout"}
    for brief in golden.briefs:  # products resolve against the shared products/ dir
        assert v3.product_file(golden, brief.product).exists()


def test_report_snapshot_path_is_repo_relative(tmp_path: Path) -> None:
    # Committed reports must not record the machine's checkout directory.
    assert repo_relative(GOLDEN_DIR / "v2" / "cache" / "verdicts.json") == (
        "data/golden/v2/cache/verdicts.json"
    )
    outside = tmp_path / "verdicts.json"  # a scratch dir outside the repository keeps its path
    assert repo_relative(outside) == str(outside)
