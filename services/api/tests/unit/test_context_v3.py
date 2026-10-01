"""ev-0.7 context (analysis R6): context_judge v3 rules and the people / body-part coherence check.
No network."""

from backend.domain.adstudio.evaluator.config import load_config
from backend.domain.adstudio.evaluator.context import (
    PEOPLE_CHECK_ID,
    compile_context_checks,
    evaluate_context,
)
from backend.domain.adstudio.vision import CheckVerdict, ContextVerdict
from backend.llm.prompts.context_judge import CONTEXT_JUDGE
from tests.golden_fixtures import golden_ad


def test_context_judge_v3_rules() -> None:
    assert CONTEXT_JUDGE.version == "3"
    system = CONTEXT_JUDGE.system
    assert "examples, not a checklist" in system and "ANY single listed item" in system
    assert "dominant impression" in system and "incidental" in system


async def test_people_check_is_compiled_and_gates() -> None:
    assert load_config().context.people_check_severity == "must"
    ad = await golden_ad()
    checks = list(ad.target.context_checks)
    people = next(c for c in checks if c.id == PEOPLE_CHECK_ID)
    assert people.severity == "must" and people.pass_on == "yes"
    assert "Answer yes if no people or body parts are shown" in people.question
    assert all(c.id != PEOPLE_CHECK_ID for c in compile_context_checks([], ["snow"]))

    def answers(people_verdict: str) -> ContextVerdict:
        return ContextVerdict(
            checks=[
                CheckVerdict(
                    id=c.id,
                    evidence="(scripted)",
                    verdict=people_verdict if c.id == PEOPLE_CHECK_ID else c.pass_on,  # type: ignore[arg-type]
                )
                for c in checks
            ]
        )

    cfg = load_config().context
    _, ok = evaluate_context(checks, answers("yes"), unavailable_reason="", cfg=cfg)
    assert ok.passed is True
    _, bad = evaluate_context(checks, answers("no"), unavailable_reason="", cfg=cfg)
    assert bad.passed is False and bad.signals["must_failed"] == [PEOPLE_CHECK_ID]
