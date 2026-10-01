"""`CreativeSpec` (ai-design §13): everything generation and evaluation need, compiled by code.

The spec is the single source for the image prompt, the text zone, the evaluator target and the
context rubric. `required_text.raw` is the brief's text byte-for-byte; no model output can change it
because models never see it and no model field is copied into it.
"""

from typing import Any, Literal

from pydantic import BaseModel, Field

from backend.domain.adstudio.events import NormBoxView, ProductScaleView, SpecSummary
from backend.domain.adstudio.image_clients import FakeScene
from backend.domain.adstudio.layout import NormBox
from backend.domain.adstudio.locale_data import HEX
from backend.domain.adstudio.prompting import AdPromptFields
from backend.domain.adstudio.resolver import GeoResolution, SeasonResolution, month_text
from backend.domain.adstudio.sizing import FRAMING_TEXT, Framing, SizeClass

SPEC_VERSION = "cs-2"  # cs-2: framing + scale from real-world size (ADR-007)


class PlannerDraft(BaseModel):
    """The creative planner's output (a text model, or the default cue table)."""

    setting: str = Field(max_length=160)
    locale_cues: list[str] = Field(min_length=2, max_length=5)
    season_cues: list[str] = Field(min_length=2, max_length=5)
    contradiction_cues: list[str] = Field(min_length=2, max_length=5)
    palette: list[str] = Field(min_length=3, max_length=5)
    lighting: str = Field(max_length=80)
    mood: str = Field(max_length=40)
    cultural_avoid: list[str] = Field(default_factory=list[str], max_length=4)
    # creative_planner v2 (ADR-007): the scene's framing and everyday objects of known size placed
    # near the product. Optional so older drafts (and the v1 golden specs) still validate; code
    # derives the product scale from them and the product's real-world size (sizing.py).
    framing: str | None = Field(default=None, max_length=20)
    scale_references: list[str] = Field(default_factory=list[str], max_length=8)


class RequiredText(BaseModel):
    raw: str = Field(description="Exactly the brief's text (NFC); rendered verbatim")
    normalized: str
    script: str
    critical_tokens: list[str]
    mode: Literal["native", "overlay_only"]
    injection_flags: list[str] = Field(default_factory=list[str])
    lines: list[str]


class TextZone(BaseModel):
    anchor: Literal["top", "bottom"]
    box: NormBox
    backdrop: Literal["solid_band"] = "solid_band"
    band_color: str = Field(pattern=HEX)
    text_color: str = Field(pattern=HEX)
    max_lines: int = Field(ge=1, le=3)


class Composition(BaseModel):
    """Where and how big the product is (ai-design §2.2, ADR-007).

    `product_scale` is the product's longest side as a fraction of the image height (the middle of
    `scale_min`..`scale_max`). Specs before cs-2 had a fixed 0.5 and none of the other fields."""

    aspect_ratio: Literal["1:1", "4:5"]
    product_anchor: Literal["lower_center", "center", "upper_center"]
    product_scale: float = Field(ge=0.1, le=0.7)
    scale_min: float | None = Field(default=None, ge=0.05, le=0.7)
    scale_max: float | None = Field(default=None, ge=0.05, le=0.7)
    framing: Framing | None = None
    size_cm: float | None = Field(default=None, description="Product's real-world largest side")
    size_class: SizeClass | None = None
    size_source: Literal["profile", "category", "class"] | None = None
    resting_surface: str | None = None
    scale_references: list[str] = Field(default_factory=list[str])
    note: str | None = None

    @property
    def sized(self) -> bool:
        return self.size_cm is not None and self.framing is not None


class RubricCheck(BaseModel):
    id: str
    dimension: Literal["context", "product"]
    question: str
    severity: Literal["must", "should"]
    pass_on: Literal["yes", "no"]


class LocalePolicy(BaseModel):
    source: Literal["market", "conservative_default"]
    avoid: list[str]
    notes: str | None = None


class CreativeSpec(BaseModel):
    spec_version: str = SPEC_VERSION
    brief_hash: str
    geo: GeoResolution
    season: SeasonResolution
    draft: PlannerDraft
    draft_source: Literal["llm", "default_table"]
    planner_model: str | None = None
    planner_note: str | None = None
    composition: Composition
    text_zone: TextZone
    required_text: RequiredText
    policy: LocalePolicy
    rubric: list[RubricCheck]
    negatives: list[str]

    @property
    def aspect_ratio(self) -> Literal["1:1", "4:5"]:
        return self.composition.aspect_ratio

    def prompt_fields(self) -> AdPromptFields:
        d = self.draft
        where = self.geo.country_name + (f" ({self.geo.city})" if self.geo.city else "")
        anchor = self.composition.product_anchor.replace("_", " ").replace("center", "centre")
        return AdPromptFields(
            aspect_ratio=self.aspect_ratio,
            country_name=where,
            effective_season=self.season.effective_season,
            months_text=month_text(self.season.months),
            setting=d.setting,
            cues=[*d.season_cues, *d.locale_cues],
            lighting=d.lighting,
            palette=d.palette,
            mood=d.mood,
            avoid=self.negatives,
            lines=self.required_text.lines,
            headline=self.required_text.raw,
            text_mode="native" if self.required_text.mode == "native" else "overlay",
            zone_anchor=self.text_zone.anchor,
            zone_height_pct=round(self.text_zone.box.height * 100),
            band_color=self.text_zone.band_color,
            text_color=self.text_zone.text_color,
            product_anchor=anchor,
            product_scale_pct=round(self.composition.product_scale * 100),
            **self._sizing_fields(),
        )

    def _sizing_fields(self) -> dict[str, Any]:
        c = self.composition
        if not c.sized or c.scale_min is None or c.scale_max is None or c.framing is None:
            return {}
        return {
            "framing_text": FRAMING_TEXT[c.framing],
            "size_cm": c.size_cm,
            "scale_min_pct": round(c.scale_min * 100),
            "scale_max_pct": round(c.scale_max * 100),
            "resting_surface": c.resting_surface or "",
            "scale_references": list(c.scale_references),
        }

    def scene(self) -> FakeScene:
        z = self.text_zone.box
        native = self.required_text.mode == "native"
        return FakeScene(
            palette=tuple(self.draft.palette),
            headline_lines=tuple(self.required_text.lines) if native else (),
            zone=(z.x0, z.y0, z.x1, z.y1),
            band_color=self.text_zone.band_color,
            text_color=self.text_zone.text_color,
            product_scale=self.composition.product_scale,
        )

    def summary(self) -> SpecSummary:
        z = self.text_zone.box
        return SpecSummary(
            source="planner" if self.draft_source == "llm" else "default_table",
            effective_season=self.season.effective_season,
            hemisphere=self.geo.hemisphere,
            months=self.season.months,
            holidays=self.season.holidays,
            rationale=self.season.rationale,
            country_name=self.geo.country_name,
            locale_cues=self.draft.locale_cues,
            avoid=self.negatives,
            text_zone=NormBoxView(x0=z.x0, y0=z.y0, x1=z.x1, y1=z.y1),
            text_lines=self.required_text.lines,
            text_mode=self.required_text.mode,
            planner_model=self.planner_model,
            product_scale=self._scale_view(),
        )

    def _scale_view(self) -> ProductScaleView | None:
        c = self.composition
        if (
            c.framing is None
            or c.scale_min is None
            or c.scale_max is None
            or c.size_cm is None
            or c.size_class is None
        ):
            return None
        return ProductScaleView(
            framing=c.framing,
            scale_min=c.scale_min,
            scale_max=c.scale_max,
            size_cm=c.size_cm,
            size_class=c.size_class,
            resting_surface=c.resting_surface,
            scale_references=list(c.scale_references),
        )
