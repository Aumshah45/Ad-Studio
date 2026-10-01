"""ADR-007 generation: real-world size -> framing -> product scale; the prompt asks for a product
photographed in the scene at its real size next to named objects (no network)."""

import json
from typing import Any

import pytest
from pydantic_ai.messages import ModelMessage, ModelResponse, ToolCallPart
from pydantic_ai.models.function import AgentInfo, FunctionModel

from backend.domain.adstudio.adprompt import prompt_fields
from backend.domain.adstudio.planner import Planner, ReferenceFacts
from backend.domain.adstudio.prompting import render_ad_prompt
from backend.domain.adstudio.sizing import (
    HERO_MAX,
    HERO_MIN,
    as_framing,
    default_framing,
    plan_scale,
    resolve_size,
    size_class_for,
    size_from_category,
)
from backend.domain.adstudio.spec import CreativeSpec
from backend.llm.calls import CallRuntime
from backend.llm.gateway import LlmGateway
from tests.conftest import make_settings

TUBE = ReferenceFacts(
    category="sunscreen lotion tube",
    dominant_colors=["#F2C200"],
    box_2d=[156, 303, 930, 692],
    size_class="small",
    approx_max_dimension_cm=15.0,
    typical_surface="a bathroom shelf",
)


def test_size_classes_and_category_fallback() -> None:
    assert [size_class_for(c) for c in (5, 10, 25, 60, 200)] == [
        "tiny",
        "small",
        "medium",
        "large",
        "xlarge",
    ]
    assert size_from_category("red ceramic coffee mug") == 10.0
    assert size_from_category("sunscreen lotion tube") == 15.0
    assert size_from_category("leather sneakers") == 28.0
    assert size_from_category("reusable water bottle") == 25.0
    assert size_from_category("mystery object") is None
    # The profile's cm wins and its class is recomputed from it (they can never disagree).
    size = resolve_size(cm=15.0, size_class="large", surface="", category="tube")
    assert size is not None and size.size_class == "small" and size.source == "profile"
    assert size.surface == "a table top"
    pp1 = resolve_size(cm=None, size_class=None, surface=None, category="ceramic mug")
    assert pp1 is not None and pp1.cm == 10.0 and pp1.source == "category"
    assert as_framing("Close-up") == "close_up" and as_framing("aerial") is None


@pytest.mark.parametrize(
    ("cm", "framing", "lo", "hi"),
    [
        (10.0, "close_up", 0.182, 0.286),  # mug: small on a tabletop
        (15.0, "close_up", 0.273, 0.429),  # sunscreen tube: no longer 45-60% of the height
        (25.0, "close_up", 0.455, 0.6),  # bottle: clamped to what fits below the text zone
        (28.0, "close_up", 0.509, 0.6),  # sneaker
        (60.0, "medium", 0.5, 0.6),  # backpack-sized
    ],
)
def test_default_scale_follows_real_size(cm: float, framing: str, lo: float, hi: float) -> None:
    plan = plan_scale(cm, None)
    assert plan.framing == framing == default_framing(cm)
    assert (plan.scale_min, plan.scale_max) == (lo, hi)
    assert HERO_MIN <= plan.scale_min < plan.scale_max <= HERO_MAX
    assert plan.note is None


def test_planner_framing_is_kept_when_it_fits_and_replaced_when_it_does_not() -> None:
    medium = plan_scale(15.0, "medium")  # a tube on a table with its surroundings: small but ok
    assert medium.framing == "medium" and medium.scale_max < 0.3
    wide = plan_scale(15.0, "wide")  # a tube in a room shot would be a speck
    assert wide.framing == "close_up" and wide.note and "wide" in wide.note
    tiny = plan_scale(3.0, None)  # below the hero band even in a close-up: a narrow band at the min
    assert tiny.scale_min == HERO_MIN and tiny.scale_max > tiny.scale_min


def _planner_model(payload: dict[str, Any], calls: list[str]) -> LlmGateway:
    def fn(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        calls.append(str(messages))
        return ModelResponse(
            parts=[ToolCallPart(tool_name=info.output_tools[0].name, args=json.dumps(payload))]
        )

    return LlmGateway(
        make_settings(), CallRuntime(), model_override=FunctionModel(fn, model_name="p")
    )


async def _spec(planner: Planner, facts: ReferenceFacts | None) -> CreativeSpec:
    resolution = await planner.resolve("IN", None, "Diwali")
    return await planner.plan(
        resolution=resolution, required_text="दिवाली सेल 20% छूट", aspect_ratio="4:5", facts=facts
    )


async def test_planner_derives_scale_from_size_and_framing() -> None:
    draft = {
        "setting": "A marble tabletop on a festive balcony, soft bokeh of lights behind",
        "locale_cues": ["marigold garlands", "rangoli"],
        "season_cues": ["lit diyas", "warm dusk light"],
        "contradiction_cues": ["snow", "bare trees"],
        "palette": ["#F4A300", "#7B2D26", "#FFF1D6"],
        "lighting": "warm dusk light",
        "mood": "festive",
        "cultural_avoid": [],
        "framing": "close-up",
        "scale_references": ["a small brass diya", "a smartphone", "a «cup»", "a fourth thing"],
    }
    calls: list[str] = []
    spec = await _spec(Planner(_planner_model(draft, calls)), TUBE)
    c = spec.composition
    assert c.framing == "close_up" and c.size_cm == 15.0 and c.size_class == "small"
    assert (c.scale_min, c.scale_max) == (0.273, 0.429)
    assert c.product_scale == pytest.approx(0.351)
    assert c.scale_references == ["a small brass diya", "a smartphone", "a cup"]  # 3, cleaned
    assert c.resting_surface == "a bathroom shelf"
    # The planner saw the product's real size and a suggested framing (code facts).
    assert '"product_size_cm": 15' in calls[0] or "product_size_cm" in calls[0]
    assert "suggested_framing" in calls[0]
    # The text zone (top band) and the product (lower centre, <= scale_max of the height, bottom
    # margin 4%) never overlap.
    assert c.product_anchor == "lower_center" and c.scale_max is not None
    assert 1.0 - 0.04 - c.scale_max > spec.text_zone.box.y1
    summary = spec.summary().product_scale
    assert summary is not None and summary.framing == "close_up" and summary.size_cm == 15.0


async def test_default_table_uses_default_framing_and_references() -> None:
    spec = await _spec(Planner(None), TUBE)
    c = spec.composition
    assert c.framing == "close_up" and c.scale_references == ["a coffee cup", "a smartphone"]
    unsized = await _spec(Planner(None), None)
    assert unsized.composition.product_scale == 0.5 and unsized.composition.framing is None
    assert unsized.summary().product_scale is None


async def test_prompt_asks_for_real_size_and_a_photographed_not_composited_product() -> None:
    spec = await _spec(Planner(None), TUBE)
    prompt = render_ad_prompt(prompt_fields(spec, TUBE))
    assert "close-up tabletop shot" in prompt
    assert "about 15 cm at its largest" in prompt
    assert "27-43% of the image height" in prompt
    assert "a coffee cup and a smartphone near it at their true real-world sizes" in prompt
    assert "PHOTOGRAPHED IN THE SCENE, NOT COMPOSITED" in prompt
    for phrase in (
        "rests on a bathroom shelf with a soft contact shadow",
        "same light direction, colour temperature",
        "perspective, camera height",
        "depth of field",
        "No cut-out outline, halo",
    ):
        assert phrase in prompt, phrase
    # Product fidelity wording is unchanged.
    assert "Reproduce it EXACTLY: same shape and proportions" in prompt
    # A spec without a known size keeps a single realistic-size line (no invented cm).
    old = render_ad_prompt(prompt_fields(await _spec(Planner(None), None), None))
    assert "about 50% of the image height" in old and " cm " not in old


def test_v1_specs_still_validate() -> None:
    """Golden v1 specs (cs-1: fixed 0.5 scale, no framing) load unchanged."""
    from backend.golden.dataset import golden_out

    manifest = golden_out("v1") / "outputs" / "manifest.jsonl"
    row = json.loads(manifest.read_text(encoding="utf-8").splitlines()[0])
    spec = CreativeSpec.model_validate(row["spec"])
    assert spec.composition.product_scale == 0.5 and not spec.composition.sized
