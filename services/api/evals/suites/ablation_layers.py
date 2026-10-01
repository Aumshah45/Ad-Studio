"""Ablation A1 (ai-design §9, on the frozen evaluator ev-0.8): the evaluator's layers separately.

Every labelled E-nat and E-plant item is scored three ways, per dimension (positive class = FAIL,
`unverified` counts as FAIL, like the meta-eval):

- **deterministic**: only the deterministic checks. Text is re-evaluated with the VLM read-back
  switched off (Tesseract + Apple Vision alone; a separate replay pass, `without_readback`);
  product is the CIEDE2000 colour check (+ the hue note, which never fails); composition is the
  box-area sanity check; technical is all deterministic. Context has no deterministic check (n/a).
  The colour and area checks measure inside the VLM's product box: the layer is "no VLM verdict",
  not "no VLM at all".
- **vlm**: only the vision judge's verdicts. Text is the blind read-back alone (fail when its
  headline-zone read differs from the copy or it reads stray text), product the count and
  checklist, context the rubric, composition the judge's checks and scale ratio. Technical has no
  VLM check (n/a).
- **combined**: the shipped evaluator.

A layer with no check for a dimension on an item leaves that item out (`not_assessed`).
"""

from collections.abc import Iterable, Sequence

from backend.domain.adstudio.evalreport import DimensionMetrics, ItemRecord, LayerVerdicts
from backend.domain.adstudio.evaluator.schemas import CheckResult, Evaluation
from evals.metrics import confusion, prf

NAME = "ablation_layers"
LAYERS = ("deterministic", "vlm", "combined")
DIMENSIONS = ("technical", "text", "product", "context", "composition")
META_SETS = ("nat", "plant")


def _from_checks(checks: Sequence[CheckResult]) -> bool | None:
    """The dimension rule on a subset: any fail -> False, any unverified -> None, else True."""
    if any(c.passed is False for c in checks):
        return False
    if any(c.passed is None for c in checks):
        return None
    return True


def _vlm_text(evaluation: Evaluation) -> bool | None | str:
    text = evaluation.dimensions.get("text")
    if text is None:
        return "na"
    signals = text.signals
    if signals.get("vlm_zone") is None:
        return "na"  # no read-back (no transcription, or an overlay-only target)
    return not (signals.get("vlm_zone_errors") or signals.get("vlm_strays"))


def layer_verdicts(combined: Evaluation, deterministic_text: Evaluation | None) -> LayerVerdicts:
    """{layer: {dimension: evaluator passes?}} for one item; a missing key = not assessed."""
    out: LayerVerdicts = {layer: {} for layer in LAYERS}
    for dim, result in combined.dimensions.items():
        out["combined"][dim] = result.passed
        checks = [c for c in combined.checks if c.dimension == dim]
        det = [c for c in checks if c.method == "deterministic"]
        vlm = [c for c in checks if c.method == "vlm"]
        if dim == "text":
            if deterministic_text is not None and "text" in deterministic_text.dimensions:
                out["deterministic"]["text"] = deterministic_text.dimensions["text"].passed
            got = _vlm_text(combined)
            if got != "na":
                out["vlm"]["text"] = got if isinstance(got, bool) else None
            continue
        # A layer that owns every check of a dimension takes the dimension's own verdict (it
        # applies severities: a context "should" check fails without failing the dimension).
        if det:
            out["deterministic"][dim] = result.passed if not vlm else _from_checks(det)
        if vlm:
            out["vlm"][dim] = result.passed if not det else _from_checks(vlm)
    return out


def _metrics(items: Iterable[ItemRecord], layer: str, dim: str) -> DimensionMetrics:
    pairs: list[tuple[bool, bool]] = []
    not_assessed = unlabelled = 0
    for item in items:
        label = (item.labels or {}).get(dim)
        if label is None:
            unlabelled += 1
            continue
        verdicts = item.layers.get(layer, {})
        if dim not in verdicts:
            not_assessed += 1
            continue
        pairs.append((label is False, verdicts[dim] is not True))
    c = confusion(pairs)
    precision, recall, f1 = prf(c)
    return DimensionMetrics(
        n=len(pairs),
        confusion=c,
        precision=precision,
        recall=recall,
        f1=f1,
        not_assessed=not_assessed,
        unlabelled=unlabelled,
    )


def compute(items: Sequence[ItemRecord]) -> dict[str, dict[str, DimensionMetrics]]:
    """{layer: {dimension: P/R/F1}} over the labelled E-nat and E-plant items."""
    meta = [i for i in items if i.set in META_SETS and i.layers]
    return {layer: {dim: _metrics(meta, layer, dim) for dim in DIMENSIONS} for layer in LAYERS}
