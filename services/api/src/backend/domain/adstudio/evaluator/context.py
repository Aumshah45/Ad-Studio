"""Context adherence (ai-design §5.4): binary checks compiled from the spec, answered by the VLM.

The checks are the spec's context rubric plus one compiled from the locale avoid list. The judge
answers each with evidence first, then yes/no/unsure. Pass rule: every *must* check gets its
passing answer; `unsure` on a must fails (flagged low_confidence). *Should* checks are recorded and
feed the score, but don't gate. No judge -> every check is `unverified` -> the run can't auto-pass.
"""

from backend.domain.adstudio.evaluator.config import ContextConfig
from backend.domain.adstudio.evaluator.schemas import CheckResult, DimensionResult
from backend.domain.adstudio.evaluator.schemas import dimension_from_checks as _dimension
from backend.domain.adstudio.spec import RubricCheck
from backend.domain.adstudio.vision import ContextCheck, ContextVerdict

AVOID_CHECK_ID = "ctx.no_avoided_elements"
# ev-0.7 (analysis R6 / E2): the v1 reason pass showed "people" as a distinct context cause
# (3 of the 5 remaining v1 context fails: a leg and shoe that don't connect, a trouser leg ending
# in a detached shoe). The rubric had no question for it.
PEOPLE_CHECK_ID = "ctx.people_coherent"
PEOPLE_QUESTION = (
    "People and body parts: in your evidence, first say for each person, hand, leg or foot where "
    "it is and how it relates to the product (wearing it, holding it, or only near it). Then "
    "answer: are they all anatomically correct and physically coherent with the product? Answer "
    "no if a product sits against a body part as if worn or held but is not really on it (a shoe "
    "overlapping or beside a leg instead of on its foot, a foot that does not reach into its "
    "shoe, an ankle or trouser hem that does not continue into the shoe), if a limb, foot, shoe "
    "or product is detached, duplicated, floating or merged, or if a pose is physically "
    "implausible. Answer yes if no people or body parts are shown."
)


def compile_context_checks(
    rubric: list[RubricCheck],
    avoid: list[str],
    *,
    avoid_severity: str = "must",
    people_severity: str | None = None,
) -> list[ContextCheck]:
    """The spec's context rubric + one avoid-list check (pass = the image shows none of them)
    + (ev-0.7, `people_severity` set) one people / body-part coherence check."""
    checks = [
        ContextCheck(id=r.id, question=r.question, severity=r.severity, pass_on=r.pass_on)
        for r in rubric
        if r.dimension == "context"
    ]
    items = [a.strip() for a in avoid if a.strip()]
    if items and all(c.id != AVOID_CHECK_ID for c in checks):
        checks.append(
            ContextCheck(
                id=AVOID_CHECK_ID,
                question=(
                    "Does the image show any of the following, which this ad must avoid: "
                    + "; ".join(items[:12])
                    + "?"
                ),
                severity="must" if avoid_severity == "must" else "should",
                pass_on="no",  # noqa: S106 - a verdict, not a password
            )
        )
    if people_severity in ("must", "should") and all(c.id != PEOPLE_CHECK_ID for c in checks):
        checks.append(
            ContextCheck(
                id=PEOPLE_CHECK_ID,
                question=PEOPLE_QUESTION,
                severity="must" if people_severity == "must" else "should",
                pass_on="yes",  # noqa: S106 - a verdict, not a password
            )
        )
    return checks


def unverified_context(reason: str) -> tuple[list[CheckResult], DimensionResult]:
    check = CheckResult(
        dimension="context", name="context_judge", method="vlm", passed=None, evidence=reason
    )
    return [check], _dimension("context", [check], score=0.0, low_confidence=True)


def evaluate_context(
    checks: list[ContextCheck],
    verdict: ContextVerdict | None,
    *,
    unavailable_reason: str,
    cfg: ContextConfig,
) -> tuple[list[CheckResult], DimensionResult]:
    if not checks:
        return unverified_context("The spec has no context rubric; context can't be judged.")
    if verdict is None:
        return unverified_context(unavailable_reason)

    answers = {v.id: v for v in verdict.checks}
    gating: list[CheckResult] = []
    advisory: list[CheckResult] = []
    should_total = should_passed = 0
    low_confidence = False
    hints: list[str] = []
    for check in checks:
        answer = answers.get(check.id)
        must = check.severity == "must"
        if answer is None:
            passed: bool | None = None
            evidence = "The judge gave no answer for this check."
        elif answer.verdict == "unsure":
            low_confidence = low_confidence or must
            passed = False if (must and cfg.unsure_must_fails) else None
            evidence = f"Judge unsure. {answer.evidence}".strip()
        else:
            passed = answer.verdict == check.pass_on
            evidence = answer.evidence
        result = CheckResult(
            dimension="context",
            name=check.id,
            method="vlm",
            passed=passed,
            evidence=evidence[:300],
            data={
                "severity": check.severity,
                "pass_on": check.pass_on,
                "question": check.question,
                "verdict": answer.verdict if answer else None,
            },
        )
        if must:
            gating.append(result)
            if passed is False:
                hints.append(check.id)
        else:
            should_total += 1
            should_passed += passed is True
            advisory.append(result)
    score = should_passed / should_total if should_total else 1.0
    dim = _dimension(
        "context",
        gating,
        score=score,
        low_confidence=low_confidence,
        signals={
            "must_failed": [c.name for c in gating if c.passed is False],
            "should_failed": [c.name for c in advisory if c.passed is False],
            "should_pass_rate": round(score, 3),
        },
        repair_hint=("failed context checks: " + ", ".join(hints)) if hints else None,
    )
    # dimension_from_checks puts every failed/unknown check's evidence in reasons; only the gating
    # ones are in `gating`, so advisory failures never fail the dimension.
    return gating + advisory, dim
