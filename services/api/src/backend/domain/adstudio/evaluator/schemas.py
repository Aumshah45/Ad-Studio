"""Evaluator output types. `Evaluation.checks` maps 1:1 to `evaluation_checks` rows."""

from typing import Any, Literal

from pydantic import BaseModel, Field

# The brief's three minimum dimensions (text, product, context) plus technical and, from ev-0.6,
# composition (ADR-007: realistic scale and natural integration).
Dimension = Literal["technical", "text", "product", "context", "composition"]
Verdict = Literal["pass", "fail", "unverified"]
DIMENSION_ORDER: tuple[Dimension, ...] = ("technical", "text", "product", "context", "composition")


class CheckResult(BaseModel):
    dimension: Dimension
    name: str
    method: Literal["deterministic", "vlm"] = "deterministic"
    passed: bool | None = Field(description="null = could not be verified")
    value: float | None = None
    threshold: float | None = None
    evidence: str = ""
    data: dict[str, Any] | None = None


class DimensionResult(BaseModel):
    dimension: Dimension
    passed: bool | None = Field(description="null = unverified (fails closed)")
    score: float = Field(ge=0.0, le=1.0)
    low_confidence: bool = False
    signals: dict[str, Any] = Field(default_factory=dict[str, Any])
    failed_checks: list[str] = Field(default_factory=list[str])
    reasons: list[str] = Field(default_factory=list[str])
    repair_hint: str | None = None


class Evaluation(BaseModel):
    image_sha: str
    evaluator_version: str
    dimensions: dict[Dimension, DimensionResult]
    checks: list[CheckResult]
    verdict: Verdict
    composite: float = Field(ge=0.0, le=1.0)
    latency_ms: int = 0
    cost_usd: float = 0.0
    vision_model: str | None = None

    @property
    def passed(self) -> bool:
        return self.verdict == "pass"


def dimension_from_checks(
    dimension: Dimension,
    checks: list[CheckResult],
    *,
    signals: dict[str, Any] | None = None,
    score: float | None = None,
    repair_hint: str | None = None,
    low_confidence: bool = False,
) -> DimensionResult:
    """A dimension fails if any check fails, is unverified if any is unverified, else passes."""
    failed = [c for c in checks if c.passed is False]
    unknown = [c for c in checks if c.passed is None]
    passed: bool | None = False if failed else (None if unknown else True)
    if score is None:
        score = sum(1 for c in checks if c.passed) / len(checks) if checks else 0.0
    return DimensionResult(
        dimension=dimension,
        passed=passed,
        score=max(0.0, min(1.0, score)),
        low_confidence=low_confidence or bool(unknown),
        signals=signals or {},
        failed_checks=[c.name for c in failed],
        reasons=[c.evidence for c in failed + unknown if c.evidence],
        repair_hint=repair_hint,
    )


def overall_verdict(dimensions: dict[Dimension, DimensionResult]) -> Verdict:
    results = [d.passed for d in dimensions.values()]
    if any(r is False for r in results):
        return "fail"
    if any(r is None for r in results) or not results:
        return "unverified"
    return "pass"
