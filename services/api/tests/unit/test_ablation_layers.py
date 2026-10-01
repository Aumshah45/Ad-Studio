"""Ablation A1 (evaluator layers) and the disputed-rulings view, on hand-built records (offline)."""

from typing import Literal

from backend.domain.adstudio.evalreport import ItemRecord, LayerVerdicts
from backend.domain.adstudio.evaluator.schemas import (
    CheckResult,
    Dimension,
    Evaluation,
    dimension_from_checks,
)
from evals.suites import ablation_layers, evaluator_meta


def _evaluation(checks: list[CheckResult], text_signals: dict[str, object]) -> Evaluation:
    dims = {}
    for dim in ("technical", "text", "product", "context", "composition"):
        mine = [c for c in checks if c.dimension == dim]
        signals = text_signals if dim == "text" else {}
        dims[dim] = dimension_from_checks(dim, mine, signals=signals)  # type: ignore[arg-type]
    return Evaluation(
        image_sha="x",
        evaluator_version="t",
        dimensions=dims,
        checks=checks,
        verdict="fail",
        composite=0.5,
    )


def _c(
    dim: Dimension,
    name: str,
    passed: bool | None,
    method: Literal["deterministic", "vlm"] = "deterministic",
) -> CheckResult:
    return CheckResult(dimension=dim, name=name, passed=passed, method=method)


def test_layer_verdicts_split_checks_by_method() -> None:
    combined = _evaluation(
        [
            _c("technical", "aspect", True),
            _c("text", "ocr_cer", True),
            _c("product", "color_delta_e", True),
            _c("product", "hue_drift", True),
            _c("product", "vlm_logo_preserved", False, "vlm"),
            _c("context", "ctx.season", True, "vlm"),
            _c("composition", "comp.scale_sanity", True),
            _c("composition", "comp.realistic_scale", None, "vlm"),
        ],
        {"vlm_zone": "Summer Sole", "vlm_zone_errors": ["'a' at 9 altered"], "vlm_strays": []},
    )
    det_text = _evaluation([_c("text", "ocr_cer", False)], {})
    layers = ablation_layers.layer_verdicts(combined, det_text)
    assert layers["combined"] == {
        "technical": True,
        "text": True,
        "product": False,
        "context": True,
        "composition": None,
    }
    assert layers["deterministic"] == {
        "technical": True,
        "text": False,  # the read-back-off re-evaluation
        "product": True,
        "composition": True,
    }
    assert layers["vlm"] == {"text": False, "product": False, "context": True, "composition": None}
    # No read-back at all: the vlm text layer has no opinion (not assessed).
    silent = _evaluation([_c("text", "ocr_cer", True)], {"vlm_zone": None})
    assert "text" not in ablation_layers.layer_verdicts(silent, None)["vlm"]


def _item(
    item_id: str, label: bool, layers: dict[str, dict[str, bool | None]], source: str | None = None
) -> ItemRecord:
    return ItemRecord(
        id=item_id,
        set="plant" if source else "nat",
        brief_id="B13",
        product_id="P4",
        split="heldout",
        image_sha=item_id,
        source_id=source,
        label_source="human",
        labels={"text": label},
        verdict="pass",
        dimensions={"text": layers["combined"].get("text")},
        layers=layers,
    )


def test_layers_metrics_and_disputed_rulings() -> None:
    fail: LayerVerdicts = {
        "combined": {"text": False},
        "deterministic": {"text": False},
        "vlm": {"text": True},
    }
    ok: LayerVerdicts = {"combined": {"text": True}, "deterministic": {"text": True}, "vlm": {}}
    items = [
        _item("B01-nat", False, fail),  # a real failure: det + combined catch it, vlm misses it
        _item("B02-nat", True, ok),
        _item("B13-nat", True, fail),  # disputed pass ruling
        _item("PL12-product_recolour", True, fail, source="B13-nat"),  # inherits it
    ]
    table = ablation_layers.compute(items)
    assert (
        table["combined"]["text"].confusion.tp == 1 and table["combined"]["text"].confusion.fp == 2
    )
    assert table["vlm"]["text"].n == 3 and table["vlm"]["text"].not_assessed == 1
    assert table["vlm"]["text"].recall == 0.0
    meta = evaluator_meta.compute(items, "v2")
    assert meta.disputed == ["B13-nat:text", "PL12-product_recolour:text"]
    assert meta.per_dimension["text"].precision == 1 / 3  # as labelled
    assert meta.per_dimension_undisputed["text"].precision == 1.0
    assert evaluator_meta.compute(items, "v1").disputed == []  # rulings are per version
