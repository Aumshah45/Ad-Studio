"""The golden eval report (slice 17): metrics are computed and present, not that they meet their
targets (plan test policy). Small synthetic labelled fixtures; no network."""

import re
from datetime import UTC, datetime
from pathlib import Path

import pytest

from backend.domain.adstudio.evalreport import (
    CheckRecord,
    EvalReportData,
    ItemRecord,
    RateMetric,
)
from backend.domain.adstudio.evaluator.color import compare_colours
from backend.domain.adstudio.evaluator.config import load_config
from backend.domain.adstudio.evaluator.product import evaluate_product
from backend.domain.adstudio.evaluator.technical import decode_for_checks
from backend.domain.adstudio.vision import FakeVisionClient
from backend.golden.dataset import REPO_DIR, OutputItem, RunInfo
from backend.golden.harness import (
    EvalCase,
    StaleSnapshotError,
    evaluate_cases,
    record_engine,
    replay_engine,
    write_verdicts,
)
from evals.golden import criteria, stability
from evals.report import render_golden_markdown, write_golden_report
from evals.suites import evaluator_meta, pipeline_quality
from tests.conftest import make_settings
from tests.golden_fixtures import golden_ad, tesseract_available

DIMS: tuple[str, ...] = ("technical", "text", "product", "context", "composition")


def rec(
    item_id: str,
    image_set: str,
    labels: dict[str, bool | None] | None,
    dims: dict[str, bool | None],
    *,
    product: str = "P1",
    mutation: str | None = None,
    source: str = "human",
    checks: list[CheckRecord] | None = None,
) -> ItemRecord:
    verdict = (
        "fail"
        if any(v is False for v in dims.values())
        else ("pass" if all(v is True for v in dims.values()) else "unverified")
    )
    return ItemRecord(
        id=item_id,
        set=image_set,  # type: ignore[arg-type]
        brief_id="B01",
        product_id=product,
        split="calibration" if product in ("P1", "P2", "P3") else "heldout",
        image_sha=item_id.ljust(64, "0"),
        mutation=mutation,
        label_source=source,  # type: ignore[arg-type]
        labels=labels,
        verdict=verdict,  # type: ignore[arg-type]
        dimensions=dims,
        checks=checks or [],
    )


ALL_PASS: dict[str, bool | None] = dict.fromkeys(DIMS, True)


def fails(*dims: str) -> dict[str, bool | None]:
    return {d: d not in dims for d in DIMS}


def meta_items() -> list[ItemRecord]:
    return [
        # text: 3 caught typos, 1 missed, 1 false alarm on a clean natural image
        *(
            rec(f"PL0{i}", "plant", fails("text"), fails("text"), mutation="text_typo")
            for i in range(3)
        ),
        rec("PL03", "plant", fails("text"), ALL_PASS, mutation="text_typo"),
        rec("N01", "nat", ALL_PASS, fails("text")),
        # product on the held-out split: 1 caught recolour, 1 correct pass
        rec(
            "PL04",
            "plant",
            fails("product"),
            fails("product"),
            product="P4",
            mutation="product_recolour",
        ),
        rec("N02", "nat", ALL_PASS, ALL_PASS, product="P4"),
        # a technical failure: the other dimensions are not assessed
        rec("PL05", "plant", fails("technical"), {"technical": False}, mutation="technical"),
        # controls
        rec("PL06", "plant", ALL_PASS, ALL_PASS, mutation="control_good"),
        rec("PL07", "plant", ALL_PASS, ALL_PASS, mutation="control_good"),
        # unlabelled natural image and an E-final image: not in the P/R set
        rec("N03", "nat", None, fails("context"), source="none"),
        rec("F01", "final", fails("context"), fails("context")),
    ]


def test_meta_eval_metrics_present(tmp_path: Path) -> None:
    meta = evaluator_meta.compute(meta_items())
    assert set(meta.per_dimension) == set(DIMS)
    text = meta.per_dimension["text"]
    assert (text.confusion.tp, text.confusion.fp, text.confusion.fn) == (3, 1, 1)
    assert text.precision == pytest.approx(0.75) and text.recall == pytest.approx(0.75)
    assert text.f1 == pytest.approx(0.75)
    assert text.not_assessed == 1 and text.unlabelled == 1  # PL05 (technical) and N03
    product = meta.per_dimension["product"]
    assert product.recall == 1.0 and product.precision == 1.0
    assert meta.per_split["heldout"]["product"].n == 2  # P4 items only
    assert meta.per_split["calibration"]["product"].n > 0
    classes = {p.mutation: p for p in meta.planted}
    assert (classes["text_typo"].caught, classes["text_typo"].n) == (3, 4)
    assert classes["text_typo"].missed == ["PL03"]
    assert classes["technical"].rate == 1.0
    assert meta.known_good == RateMetric(n=2, ok=2, rate=1.0)
    by_id = {i.id: i for i in meta.items}
    assert by_id["N01"].outcomes["text"] == "fp" and by_id["PL03"].outcomes["text"] == "fn"
    assert by_id["PL05"].outcomes["text"] is None

    data = _report(meta)
    md = render_golden_markdown(data)
    for dim in DIMS:
        assert re.search(rf"^\| {dim} \| \d+ \| \d+ \| \d+ \| \d+ \| \d+ \|", md, re.M), dim
        assert f"**{dim}**" in md  # its confusion matrix
    path = write_golden_report(data, reports_dir=tmp_path)
    assert path.exists() and (tmp_path / "latest.json").exists()
    assert (tmp_path / "baseline.json").exists()
    assert EvalReportData.model_validate_json((tmp_path / "latest.json").read_text()) == data


def _report(meta: evaluator_meta.MetaResult) -> EvalReportData:
    pipeline = pipeline_quality.compute([], {}, [])
    crit = criteria(meta, pipeline, RateMetric(n=12, ok=12, rate=1.0), 0, {})
    return EvalReportData(
        report_file="20260101T000000Z.md",
        created_at=datetime(2026, 1, 1, tzinfo=UTC),
        evaluator_version="ev-test",
        dataset_version="abc",
        dry_run=True,
        per_dimension=meta.per_dimension,
        per_split=meta.per_split,
        human_agreement=meta.human_agreement,
        agreement=meta.agreement,
        planted=meta.planted,
        known_good=meta.known_good,
        pipeline=pipeline,
        criteria=crit,
        items=meta.items,
    )


def test_human_agreement_computed() -> None:
    items = [
        rec("N1", "nat", ALL_PASS, ALL_PASS),
        rec("N2", "nat", ALL_PASS, ALL_PASS),
        rec("N3", "nat", fails("text"), fails("text")),
        rec("N4", "nat", fails("context"), fails("product")),  # both fail overall: agree
        rec("N5", "nat", ALL_PASS, fails("product")),  # disagree
        rec("N6", "nat", ALL_PASS, fails("text"), source="assumed"),  # not a human label
        rec("N7", "nat", None, ALL_PASS, source="none"),
    ]
    meta = evaluator_meta.compute(items)
    ha = meta.human_agreement
    assert (ha.n, ha.agree) == (5, 4) and ha.agreement == pytest.approx(0.8)
    # human pass: 3/5, evaluator pass: 2/5 -> p_e = 0.48, kappa = (0.8 - 0.48) / 0.52
    assert ha.kappa == pytest.approx((0.8 - 0.48) / 0.52)
    text = meta.agreement["nat"]["text"]
    assert text.n == 5 and text.agreement == pytest.approx(1.0)
    product = meta.agreement["nat"]["product"]
    assert product.agreement == pytest.approx(3 / 5)
    assert meta.agreement["final"]["text"].n == 0


def _final(brief: str, status: str, outcome: str | None, **kw: object) -> OutputItem:
    return OutputItem.model_validate(
        {
            "id": f"{brief}-final",
            "set": "final",
            "brief_id": brief,
            "product_id": "P1",
            "golden_key": f"{brief}@t@fake",
            "file": f"{brief}-final.png",
            "image_sha": brief.ljust(64, "0"),
            "width": 819,
            "height": 1024,
            "reference_sha": "0" * 64,
            "spec": {},
            "candidate_kind": "overlay" if outcome == "overlay" else "initial",
            "attempt": 1,
            "slot": 0,
            "candidate_status": "passed",
            "image_client": "fake",
            "pipeline_version": "t",
            "run": RunInfo(
                run_id=brief,
                status=status,
                outcome=outcome,
                first_attempt_pass=outcome == "native",
                repair_count=0 if outcome == "native" else 1,
                cost_usd=0.1,
                latency_ms=30_000,
            ),
            **kw,
        }
    )


def _cer(value: float) -> list[CheckRecord]:
    return [CheckRecord(dimension="text", name="ocr_cer", passed=value == 0, value=value)]


def test_shipped_text_exact() -> None:
    outputs = [
        _final("B01", "passed", "native"),
        _final("B17", "passed", "overlay", text_verification="construction"),
        _final("B20", "approved", "overlay", exclude_native_text=True),
        _final("B05", "needs_review", None),
    ]
    evaluated = {
        "B01-final": rec("B01-final", "final", None, ALL_PASS, checks=_cer(0.0)),
        "B17-final": rec("B17-final", "final", None, ALL_PASS),  # no OCR pack: unverified text
        "B20-final": rec("B20-final", "final", None, ALL_PASS, checks=_cer(0.0)),
        "B05-final": rec("B05-final", "final", None, fails("text"), checks=_cer(0.3)),
    }
    m = pipeline_quality.compute(outputs, evaluated, [(819, 1024), (1024, 1024)])
    assert m.shipped == 3 and m.after_repair_pass_rate == pytest.approx(0.75)
    assert (m.text_exact_ocr, m.text_exact_construction) == (2, 1)
    assert m.text_exact_rate == 1.0
    assert m.native_text_n == 2 and m.native_text_rate == pytest.approx(0.5)  # B20 excluded
    assert m.cost_per_approved_ad == pytest.approx(0.4 / 3)
    assert m.p50_latency_ms == 30_000 and m.resolution_ok_rate == 1.0
    assert m.minor_label_detail_rate == 0.0 and m.minor_label_detail_n == 4

    note = CheckRecord(
        dimension="product", name="vlm_label_subtext_preserved", passed=True, note=True
    )
    evaluated["B17-final"] = rec("B17-final", "final", None, ALL_PASS, checks=[note])
    noted = pipeline_quality.compute(outputs, evaluated, [])
    assert noted.minor_label_detail_rate == pytest.approx(0.25)  # a note, not a failure
    assert noted.shipped == 3

    evaluated["B01-final"] = rec("B01-final", "final", None, fails("text"), checks=_cer(0.05))
    worse = pipeline_quality.compute(outputs, evaluated, [])
    assert worse.text_exact_rate == pytest.approx(2 / 3)  # one shipped ad is not exact


def _prd_criteria() -> list[str]:
    prd = (REPO_DIR / "docs" / "prd.md").read_text(encoding="utf-8")
    section = prd.split("## Success criteria", 1)[1].split("\n## ", 1)[0]
    rows = [line for line in section.splitlines() if line.startswith("| ")]
    return [r.split("|")[1].strip() for r in rows[1:] if not set(r) <= {"|", "-", " "}]


def test_report_has_all_prd_criteria() -> None:
    names = _prd_criteria()
    assert len(names) >= 10
    meta = evaluator_meta.compute(meta_items())
    data = _report(meta)
    covered = {c.criterion for c in data.criteria}
    assert set(names) <= covered, set(names) - covered
    md = render_golden_markdown(data)
    assert "| Criterion | Metric | Target | Baseline | Now | Status |" in md
    for c in data.criteria:
        assert c.target, c.id
        assert c.baseline == c.now  # the first report is its own baseline
    with_baseline = criteria(
        meta, data.pipeline, data.hemisphere, 0, {"recall.text": 0.5, "network_calls": 0.0}
    )
    recall = next(c for c in with_baseline if c.id == "recall.text")
    assert recall.baseline == 0.5 and recall.now == pytest.approx(0.75) and recall.met is False


async def test_cv_determinism() -> None:
    """The deterministic product check (seeded k-means + CIEDE2000) gives identical results."""
    ad = await golden_ad("P3", "ZA", "June", "Winter Run Club")
    cfg = load_config().product
    img, ref = decode_for_checks(ad.data), decode_for_checks(ad.reference)
    assert img is not None and ref is not None
    first = compare_colours(ref, None, img, ad.box, cfg)
    again = compare_colours(ref, None, img, ad.box, cfg)
    assert first == again
    inspection = await FakeVisionClient(box=ad.box).inspect(ad.reference, ad.data, summary="")
    runs = [
        evaluate_product(
            img, ref, reference_box=None, inspection=inspection, unavailable_reason="", cfg=cfg
        )
        for _ in range(2)
    ]
    assert [c.model_dump() for c in runs[0][0]] == [c.model_dump() for c in runs[1][0]]
    assert runs[0][1].model_dump() == runs[1][1].model_dump()


def test_judge_stability_counts_flips() -> None:
    vlm = [
        CheckRecord(dimension="context", name="ctx.a", method="vlm", passed=True),
        CheckRecord(dimension="context", name="ctx.b", method="vlm", passed=True),
    ]
    flipped = [vlm[0], vlm[1].model_copy(update={"passed": False})]
    one = rec("N1", "nat", None, ALL_PASS, checks=vlm)
    two = rec("N1", "nat", None, ALL_PASS, checks=flipped)
    s = stability([one], [two])
    assert (s.items, s.checks, s.flips) == (1, 2, 1) and s.flip_rate == 0.5


async def _case() -> EvalCase:
    ad = await golden_ad()
    return EvalCase(
        id="B01-nat",
        set="nat",
        brief_id="B01",
        product_id="P1",
        image_sha="x",
        image=ad.data,
        reference=ad.reference,
        target=ad.target,
        labels=None,
        label_source="none",
    )


async def test_replay_fails_loudly_on_a_stale_snapshot(tmp_path: Path) -> None:
    snapshot = tmp_path / "verdicts.json"
    snapshot.write_text('{"format": 1, "entries": {}}')
    engine = replay_engine(snapshot, make_settings())
    with pytest.raises(StaleSnapshotError):
        await evaluate_cases(engine, [await _case()])


@pytest.mark.skipif(not tesseract_available(), reason="recording needs the tesseract binary")
async def test_record_then_replay_gives_the_same_verdicts(tmp_path: Path) -> None:
    case = await _case()
    recorder = record_engine(
        make_settings(vision_client="fake"), base=None, sessionmaker=None, allow_live=True
    )
    (recorded,) = await evaluate_cases(recorder, [case])
    snapshot = tmp_path / "verdicts.json"
    assert write_verdicts(snapshot, recorder, keep_existing=False) > 0
    engine = replay_engine(snapshot, make_settings())
    (replayed,) = await evaluate_cases(engine, [case])
    assert replayed.evaluation.verdict == recorded.evaluation.verdict == "pass"
    assert [c.model_dump() for c in replayed.evaluation.checks] == [
        c.model_dump() for c in recorded.evaluation.checks
    ]


def test_human_agreement_compares_only_the_labelled_dimensions() -> None:
    """Rubric-1 labels have no composition: the evaluator's composition verdict is left out of the
    overall comparison instead of counting as a disagreement; rubric-2 labels include it."""
    core: dict[str, bool | None] = {
        "technical": True,
        "text": True,
        "product": True,
        "context": True,
    }
    comp_fail: dict[str, bool | None] = {**core, "composition": False}
    items = [
        rec("N1", "nat", dict(core), comp_fail),  # rubric 1: agree (composition not labelled)
        rec("N2", "nat", {**core, "composition": True}, comp_fail),  # rubric 2: disagree
        rec("N3", "nat", {**core, "composition": False}, comp_fail),  # rubric 2: agree
    ]
    ha = evaluator_meta.human_agreement(items)
    assert (ha.n, ha.agree) == (3, 2)
    comp = evaluator_meta.dimension_metrics(items, "composition")
    assert (
        comp.n == 2 and comp.unlabelled == 1 and comp.confusion.tp == 1 and comp.confusion.fp == 1
    )
