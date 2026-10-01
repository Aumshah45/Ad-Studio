"""Metric arithmetic for the golden eval (ai-design §9.2). Positive class = FAIL."""

import math
from collections.abc import Iterable, Sequence

from backend.domain.adstudio.evalreport import Confusion, Outcome


def outcome(label_fail: bool, pred_fail: bool) -> Outcome:
    if label_fail:
        return "tp" if pred_fail else "fn"
    return "fp" if pred_fail else "tn"


def confusion(pairs: Iterable[tuple[bool, bool]]) -> Confusion:
    """`pairs` are (label says FAIL, evaluator says FAIL)."""
    c = Confusion()
    for label_fail, pred_fail in pairs:
        kind = outcome(label_fail, pred_fail)
        setattr(c, kind, getattr(c, kind) + 1)
    return c


def ratio(num: float, den: float) -> float | None:
    return num / den if den else None


def prf(c: Confusion) -> tuple[float | None, float | None, float | None]:
    precision = ratio(c.tp, c.tp + c.fp)
    recall = ratio(c.tp, c.tp + c.fn)
    if precision is None or recall is None:
        f1 = None
    else:
        f1 = 0.0 if precision + recall == 0 else 2 * precision * recall / (precision + recall)
    return precision, recall, f1


def agreement(pairs: Sequence[tuple[bool, bool]]) -> tuple[int, float | None]:
    agree = sum(1 for a, b in pairs if a == b)
    return agree, ratio(agree, len(pairs))


def cohen_kappa(pairs: Sequence[tuple[bool, bool]]) -> float | None:
    """κ = (p_o − p_e) / (1 − p_e) for two binary raters. None when undefined (no items, or both
    raters constant and equal so p_e = 1)."""
    n = len(pairs)
    if n == 0:
        return None
    p_o = sum(1 for a, b in pairs if a == b) / n
    pa = sum(1 for a, _ in pairs if a) / n
    pb = sum(1 for _, b in pairs if b) / n
    p_e = pa * pb + (1 - pa) * (1 - pb)
    if math.isclose(p_e, 1.0):
        return None
    return (p_o - p_e) / (1 - p_e)


def percentile(values: Sequence[float], q: float) -> float | None:
    """Linear interpolation between closest ranks (numpy's default)."""
    if not values:
        return None
    ordered = sorted(values)
    pos = (len(ordered) - 1) * q / 100
    lo, hi = math.floor(pos), math.ceil(pos)
    return ordered[lo] + (ordered[hi] - ordered[lo]) * (pos - lo)


def mean(values: Sequence[float]) -> float | None:
    return sum(values) / len(values) if values else None
