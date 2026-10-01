"""Orchestrator building blocks: the per-run budget, selection/base choice and the fake's script."""

import io
import uuid
from typing import Any, cast

import pytest
from PIL import Image

from backend.core.errors import AppError
from backend.domain.adstudio.budget import RunBudget, image_price
from backend.domain.adstudio.evaluator.schemas import (
    CheckResult,
    Dimension,
    DimensionResult,
    Evaluation,
    dimension_from_checks,
    overall_verdict,
)
from backend.domain.adstudio.image_clients import (
    FakeImageClient,
    FakeScene,
    ImageRequest,
    parse_fake_script,
    typo_line,
)
from backend.domain.adstudio.pipeline import Scored, choose_base, select_best
from backend.llm.calls import SafetyBlockedError
from backend.llm.ledger import bind_run
from tests.images import png

LITE = "google:gemini-3.1-flash-lite-image"
FLASH = "google:gemini-3.1-flash-image"


class Clock:
    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        return self.now


def test_run_budget_reserves_list_price_and_stops() -> None:
    clock = Clock()
    budget = RunBudget(cap_usd=0.25, max_image_calls=5, deadline_s=150, clock=clock)
    assert image_price(LITE) == pytest.approx(0.034) and image_price(FLASH) == pytest.approx(0.067)
    assert image_price("test:fake-image") == 0.0
    for _ in range(2):
        budget.reserve(LITE)
    budget.reserve(FLASH)
    budget.reserve(FLASH)
    assert budget.spent_usd == pytest.approx(0.202) and budget.image_calls == 4
    assert budget.blocker(FLASH) == "budget"  # 0.202 + 0.067 > 0.25
    assert budget.blocker(LITE) is None  # 0.236 fits
    with pytest.raises(AppError) as exc:
        budget.reserve(FLASH)
    assert exc.value.type == "budget-exceeded"
    budget.other_usd = 0.02  # vision/text spend from the ledger counts too
    assert budget.blocker(LITE) == "budget"
    budget.release(0.067)  # a cache hit costs nothing
    assert budget.blocker(LITE) is None

    calls = RunBudget(cap_usd=10, max_image_calls=1, deadline_s=150, clock=clock)
    calls.reserve(LITE)
    assert calls.blocker(LITE) == "image_calls"
    clock.now = 151
    assert budget.expired and budget.blocker(LITE) == "deadline"


def test_run_budget_warns_once_past_eighty_percent() -> None:
    budget = RunBudget(cap_usd=0.1, max_image_calls=5, deadline_s=150)
    budget.reserve(LITE)
    assert not budget.should_warn()
    budget.reserve(LITE)  # 0.068 < 0.08
    assert not budget.should_warn()
    budget.other_usd = 0.015
    assert budget.should_warn() and not budget.should_warn()


def _evaluation(passes: dict[str, bool | None], composite: float) -> Evaluation:
    dims: dict[Dimension, DimensionResult] = {}
    for name, passed in passes.items():
        dim = cast(Dimension, name)
        dims[dim] = dimension_from_checks(
            dim, [CheckResult(dimension=dim, name=f"{name}_check", passed=passed)]
        )
    return Evaluation(
        image_sha="0" * 64,
        evaluator_version="test",
        dimensions=dims,
        checks=[],
        verdict=overall_verdict(dims),
        composite=composite,
    )


def _scored(order: int, composite: float, **passes: bool | None) -> Scored:
    full: dict[str, bool | None] = {"technical": True, "text": True, "product": True}
    full["context"] = True
    full.update(passes)
    return Scored(
        candidate=cast(Any, None),
        image=cast(Any, None),
        data=b"",
        evaluation=_evaluation(full, composite),
        order=order,
    )


def test_select_best_takes_highest_composite_ties_by_order() -> None:
    a = _scored(0, 0.9)
    b = _scored(1, 0.95)
    c = _scored(2, 0.95)
    failing = _scored(3, 1.0, text=False)
    assert select_best([a, b, c, failing]) is b
    assert select_best([failing]) is None
    assert select_best([_scored(0, 0.9, context=None)]) is None  # unverified never passes


def test_choose_base_prefers_fewest_non_text_failures() -> None:
    text_only = _scored(0, 0.5, text=False)
    product = _scored(1, 0.9, product=False)
    both = _scored(2, 0.99, product=False, context=False)
    technical = _scored(3, 1.0, technical=False)
    assert choose_base([both, product, technical, text_only]) is text_only
    assert choose_base([both, product]) is product
    tie_a, tie_b = _scored(0, 0.7, text=False), _scored(1, 0.8, text=False)
    assert choose_base([tie_a, tie_b]) is tie_b


def test_parse_fake_script_and_typo() -> None:
    script = parse_fake_script("candidate=typo; repair_text=typo,ok ;candidate.1=ok")
    assert script == {
        "candidate": ("typo",),
        "repair_text": ("typo", "ok"),
        "candidate.1": ("ok",),
    }
    assert parse_fake_script("") == {}
    with pytest.raises(ValueError, match="unknown label"):
        parse_fake_script("nonsense=typo")
    with pytest.raises(ValueError, match="unknown behaviour"):
        parse_fake_script("candidate=explode")
    assert typo_line("Summer Sale — 30% OFF") == "Summr Sale — 30% OFF"
    assert typo_line("Hi 5") != "Hi 5"


def _request(label: str, slot: int = 0, lines: tuple[str, ...] = ("Summer Sale",)) -> ImageRequest:
    return ImageRequest(
        prompt=f"p-{label}",
        images=(png(300, 400, (200, 30, 40)),),
        aspect_ratio="4:5",
        scene=FakeScene(headline_lines=lines),
        label=label,
        slot=slot,
    )


async def test_fake_script_counts_per_run_and_label() -> None:
    fake = FakeImageClient(script="candidate.1=blank;repair_text=typo,ok;clean_plate=block")
    with bind_run(uuid.uuid4()):
        clean = await fake.generate(_request("candidate", 0), model="m")
        blank = await fake.generate(_request("candidate", 1), model="m")
        first_edit = await fake.generate(_request("repair_text"), model="m")
        second_edit = await fake.generate(_request("repair_text"), model="m")
        with pytest.raises(SafetyBlockedError):
            await fake.generate(_request("clean_plate"), model="m")
    with bind_run(uuid.uuid4()):  # a new run replays the script from the start
        again = await fake.generate(_request("repair_text"), model="m")
    assert [b for _, b in fake.behaviours] == ["ok", "blank", "typo", "ok", "block", "typo"]
    assert clean.meta["behaviour"] == "ok" and again.meta["behaviour"] == "typo"
    with Image.open(io.BytesIO(blank.data)) as img:
        extrema = cast(tuple[tuple[int, int], ...], img.getextrema())
        assert all(125 <= lo <= hi <= 131 for lo, hi in extrema)  # flat grey
    assert first_edit.data != second_edit.data
    assert fake.cache_tag and FakeImageClient().cache_tag == ""
