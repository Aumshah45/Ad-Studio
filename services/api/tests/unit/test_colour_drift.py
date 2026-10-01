"""ev-0.8 / ad_generate v6: product colours by hue family in the prompt, and a hue-drift note that
is measured but never fails (user ruling: red-vs-pink is acceptable shade variation).

Regression: v5 named P4's pink bottle (#DC2E63) by the nearest RGB anchor, "red", so every P4 ad
asked for (and drew) a red bottle (golden v2, analysis E6)."""

import pytest

from backend.domain.adstudio.evaluator.core import Evaluator
from backend.domain.adstudio.planner import ReferenceFacts
from backend.domain.adstudio.prompting import (
    AdPromptFields,
    color_name,
    hue_family,
    product_colour_line,
    render_ad_prompt,
)
from backend.domain.adstudio.repair import FailureCode, product_problems, render_repair_product
from backend.domain.adstudio.vision import FakeVisionClient
from backend.llm.prompts.ad_generate import AD_GENERATE
from backend.llm.prompts.ad_repair import AD_REPAIR_PRODUCT
from tests.unit.test_vision_evaluator import (
    RIGHT,
    fixture_ad,
    hue_shift,
    product_box_px,
    product_photo,
    target,
)


@pytest.mark.parametrize(
    ("hex_", "name"),
    [
        ("#DC2E63", "pink"),  # P4 bottle (v5 called it "red")
        ("#E0457B", "pink"),
        ("#E51922", "red"),  # P1 mug print
        ("#B92C28", "red"),  # P3 sneakers
        ("#C1945A", "tan"),  # P2 peanuts
        ("#3D281A", "brown"),  # P5 tube
        ("#E6DE8A", "pale yellow"),  # P5 cap
        ("#494A49", "charcoal"),
        ("#EBEAEA", "white"),
        ("#0D3B66", "dark blue"),
    ],
)
def test_product_colours_are_named_by_hue(hex_: str, name: str) -> None:
    assert hue_family(hex_)[0] == name


def test_v5_named_the_pink_bottle_red() -> None:
    assert color_name("#DC2E63") == "red"  # why v6 exists


def _fields(colors: list[str]) -> AdPromptFields:
    return AdPromptFields(
        aspect_ratio="4:5",
        country_name="United Arab Emirates",
        effective_season="hot",
        months_text="August",
        setting="a rooftop terrace",
        cues=["palm trees"],
        lighting="bright sun",
        palette=["#F4D35E"],
        mood="fresh",
        avoid=["snow"],
        lines=["Stay Cool. Only AED 49"],
        category="reusable water bottle",
        dominant_colors=colors,
    )


def test_image_prompt_names_the_hue_and_what_it_is_not() -> None:
    assert AD_GENERATE.version == "6"
    prompt = render_ad_prompt(_fields(["#DC2E63", "#494A49"]))
    assert "same colours (pink #DC2E63, charcoal #494A49)" in prompt
    assert "COLOURS: the main colour is pink (#DC2E63), not red or magenta" in prompt
    assert "the other colours are charcoal (#494A49)" in prompt
    # An achromatic main colour gets no "not ..." clause; no colours -> the reference decides.
    assert ", not " not in product_colour_line(["#EBEAEA"])
    assert "exactly as in the reference" in render_ad_prompt(_fields([]))


def test_product_repair_prompt_uses_the_same_colour_names() -> None:
    assert AD_REPAIR_PRODUCT.version == "3"
    facts = ReferenceFacts(category="reusable water bottle", dominant_colors=["#DC2E63"])
    drift = FailureCode(
        code="PRODUCT_COLOR_DRIFT",
        check="color_delta_e",
        value=20.0,
        got="#C81E32",
        expected="#DC2E63",
    )
    assert "red (#C81E32) instead of pink (#DC2E63)" in product_problems([drift])[0]
    prompt = render_repair_product([drift], facts)
    assert "the main colour is pink (#DC2E63), not red or magenta" in prompt


async def test_hue_drift_is_measured_as_a_note_and_never_fails() -> None:
    evaluator = Evaluator(RIGHT, vision=FakeVisionClient())
    clean = await evaluator.evaluate(fixture_ad(), product_photo(), target())
    note = next(c for c in clean.checks if c.name == "hue_drift")
    assert note.passed is True and note.data and note.data["note"] is False
    assert note.value is not None and note.value < 10

    import io

    from PIL import Image

    from backend.domain.adstudio.imaging import postprocess_generated

    raw = Image.open(io.BytesIO(fixture_ad())).convert("RGB")
    scale = raw.width / 928  # fixture_ad is post-processed from the fake's native 928 px width
    x0, y0, x1, y1 = (round(v * scale) for v in product_box_px())
    box = (x0, y0, x1, y1)
    shifted = hue_shift(raw, box, shift=12)  # about 17 degrees of hue: red towards orange
    buf = io.BytesIO()
    shifted.save(buf, format="PNG")
    drifted = await evaluator.evaluate(
        postprocess_generated(buf.getvalue()).data, product_photo(), target()
    )
    note = next(c for c in drifted.checks if c.name == "hue_drift")
    assert note.passed is True and note.data and note.data["note"] is True
    assert note.value is not None and note.value >= 10 and "hue drift" in note.evidence
    assert "hue_drift" not in drifted.dimensions["product"].failed_checks
    assert drifted.dimensions["product"].signals["hue_drift_deg"] == note.value
