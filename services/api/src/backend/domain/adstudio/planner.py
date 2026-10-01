"""Planner: brief + (optional) product facts -> `CreativeSpec` (ai-design §2 steps 3-6).

1. Resolve geography and season from code tables (a model only for an unknown season term, on
   wrapped untrusted text; otherwise 422 `unknown-season`).
2. Creative cues from the `creative_planner` text model, given only code-resolved facts. The
   required text is never sent to it. If no model is configured or the call fails, the
   deterministic default cue table is used instead.
3. Compile the spec in code: text zone, line breaks, locale policy, negatives and the context
   rubric. `required_text.raw` is copied from the brief, never from a model.
"""

import hashlib
import json
import re
from dataclasses import dataclass
from typing import Any, Literal, cast

import structlog
from pydantic import BaseModel, ValidationError

from backend.domain.adstudio.layout import MAX_LINES, NormBox, break_lines, default_zone
from backend.domain.adstudio.locale_data import (
    EffectiveSeason,
    cue_table,
    locale_policy_table,
    season_table,
)
from backend.domain.adstudio.prompting import hex_to_rgb
from backend.domain.adstudio.resolver import (
    GeoResolution,
    SeasonResolution,
    holiday_months,
    holiday_row,
    named_season,
    resolve_geography,
    resolve_season,
    season_from_months,
    unknown_season,
)
from backend.domain.adstudio.sizing import (
    DEFAULT_SCALE_REFERENCES,
    ProductSize,
    as_framing,
    default_framing,
    plan_scale,
    resolve_size,
)
from backend.domain.adstudio.spec import (
    Composition,
    CreativeSpec,
    LocalePolicy,
    PlannerDraft,
    RequiredText,
    RubricCheck,
    TextZone,
)
from backend.domain.adstudio.textnorm import critical_tokens, detect_script, normalise
from backend.guardrails.input import check_input, injection_signals
from backend.llm.gateway import LlmGateway
from backend.llm.prompts.base import Prompt
from backend.llm.prompts.creative_planner import CREATIVE_PLANNER
from backend.llm.prompts.season_resolve import SEASON_RESOLVE

log = structlog.get_logger(__name__)
_HEX = re.compile(r"^#[0-9A-Fa-f]{6}$")
_UNSAFE = re.compile(r"[«»‹›<>\"`{}\[\]\r\n]+")
WIDE_PRODUCT_ASPECT = 1.6


class ReferenceFacts(BaseModel):
    """The subset of the product profile the planner uses (filled by the vision slice)."""

    category: str | None = None
    dominant_colors: list[str] = []
    box_2d: list[int] | None = None  # [ymin, xmin, ymax, xmax], 0..1000
    visible_text: list[str] = []
    # product_profile v2 (pp-2); absent on older facts (sizing then falls back to the category)
    size_class: str | None = None
    approx_max_dimension_cm: float | None = None
    typical_surface: str = ""

    def size(self) -> ProductSize | None:
        return resolve_size(
            cm=self.approx_max_dimension_cm,
            size_class=self.size_class,
            surface=self.typical_surface,
            category=self.category,
        )


class SeasonGuess(BaseModel):
    months: list[int] = []
    named_season: (
        Literal["spring", "summer", "autumn", "winter", "tropical_wet", "tropical_dry"] | None
    ) = None
    holiday_id: str | None = None
    confidence: Literal["high", "low"] = "low"


@dataclass(frozen=True)
class Resolution:
    geo: GeoResolution
    season: SeasonResolution

    def to_json(self) -> dict[str, Any]:
        return {
            "geo": self.geo.model_dump(mode="json"),
            "season": self.season.model_dump(mode="json"),
        }

    @classmethod
    def from_json(cls, data: dict[str, Any]) -> "Resolution":
        return cls(
            GeoResolution.model_validate(data["geo"]),
            SeasonResolution.model_validate(data["season"]),
        )


def parse_facts(raw: dict[str, Any] | None) -> ReferenceFacts | None:
    if not raw:
        return None
    try:
        return ReferenceFacts.model_validate(raw)
    except ValidationError:
        return None


# --- required text, colours, zone -----------------------------------------------------------


def build_required_text(raw: str) -> RequiredText:
    flags = injection_signals(raw)
    return RequiredText(
        raw=raw,
        normalized=normalise(raw),
        script=detect_script(raw),
        critical_tokens=critical_tokens(raw),
        # Flagged text never goes to any model: clean plates + deterministic overlay (RT-01/02).
        mode="overlay_only" if flags else "native",
        injection_flags=flags,
        lines=break_lines(raw),
    )


def _luminance(color: str) -> float:
    def channel(c: int) -> float:
        s = c / 255
        return s / 12.92 if s <= 0.03928 else ((s + 0.055) / 1.055) ** 2.4

    r, g, b = hex_to_rgb(color)
    return 0.2126 * channel(r) + 0.7152 * channel(g) + 0.0722 * channel(b)


def contrast_ratio(a: str, b: str) -> float:
    la, lb = sorted((_luminance(a), _luminance(b)), reverse=True)
    return (la + 0.05) / (lb + 0.05)


def pick_text_color(band: str) -> str:
    """Black or white, whichever contrasts more with the band (WCAG; >= 4.5 when possible)."""
    return max(("#000000", "#FFFFFF"), key=lambda c: contrast_ratio(band, c))


def _text_zone(aspect: str, palette: list[str], facts: ReferenceFacts | None) -> TextZone:
    anchor: Literal["top", "bottom"] = "top"
    if facts and facts.box_2d and len(facts.box_2d) == 4:
        ymin, xmin, ymax, xmax = facts.box_2d
        if ymax > ymin and (xmax - xmin) / (ymax - ymin) > WIDE_PRODUCT_ASPECT:
            anchor = "bottom"
    product_dark = bool(
        facts
        and facts.dominant_colors
        and _luminance(facts.dominant_colors[0]) < 0.08
        and _HEX.match(facts.dominant_colors[0])
    )
    by_lum = sorted(palette, key=_luminance)
    band = by_lum[-1] if product_dark else by_lum[0]
    box: NormBox = default_zone(aspect, anchor)
    return TextZone(
        anchor=anchor,
        box=box,
        band_color=band.upper(),
        text_color=pick_text_color(band),
        max_lines=MAX_LINES,
    )


# --- locale policy and rubric ---------------------------------------------------------------


def _dedup(items: list[str]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for item in items:
        key = item.strip().casefold()
        if key and key not in seen:
            seen.add(key)
            out.append(item.strip())
    return out


def compile_policy(geo: GeoResolution, holiday_ids: list[str]) -> LocalePolicy:
    table = locale_policy_table()
    avoid = list(table.default.avoid)
    notes: str | None = None
    if not geo.known:
        avoid += table.conservative.avoid
        notes = table.conservative.notes
    elif geo.country_code in table.countries:
        avoid += table.countries[geo.country_code].avoid
    for hid in holiday_ids:
        if hid in table.holidays:
            avoid += table.holidays[hid].avoid
    return LocalePolicy(
        source="market" if geo.known else "conservative_default",
        avoid=_dedup(avoid),
        notes=notes,
    )


def _rubric(
    check_id: str,
    question: str,
    severity: Literal["must", "should"],
    passes_on: Literal["yes", "no"],
) -> RubricCheck:
    return RubricCheck.model_validate(
        {
            "id": check_id,
            "dimension": "context",
            "question": question,
            "severity": severity,
            "pass_on": passes_on,
        }
    )


def _eg(items: list[str]) -> str:
    return ", ".join(items[:4])


def compile_rubric(
    geo: GeoResolution,
    season: SeasonResolution,
    draft: PlannerDraft,
    policy: LocalePolicy,
) -> list[RubricCheck]:
    label = season.effective_season.replace("_", " ")
    known_season = season.effective_season != "unspecified"
    avoid = _dedup([*draft.cultural_avoid, *policy.avoid])
    checks = [
        _rubric(
            "ctx.season_cues",
            (
                f"Does the scene show cues of {label} in {geo.country_name} "
                f"(e.g. {_eg(draft.season_cues)})?"
            ),
            "must" if known_season else "should",
            "yes",
        ),
        _rubric(
            "ctx.no_season_contradiction",
            (
                f"Does the scene contain anything contradicting {label}, such as "
                f"{_eg(draft.contradiction_cues)}?"
            ),
            "must" if known_season else "should",
            "no",
        ),
        _rubric(
            "ctx.geo_plausible",
            (f"Is the setting plausible for {geo.country_name} (e.g. {_eg(draft.locale_cues)})?"),
            "must" if geo.known else "should",
            "yes",
        ),
        _rubric(
            "ctx.no_geo_contradiction",
            ("Does the scene clearly show another country (landmarks, signage scripts, flags)?"),
            "should",
            "no",
        ),
        _rubric(
            "ctx.product_hero",
            "Is the product the clear focal point, in focus, and not cropped awkwardly?",
            "must",
            "yes",
        ),
        _rubric(
            "ctx.ad_composition",
            (
                "Does it read as a clean display ad (uncluttered, space for the headline, "
                "professional)?"
            ),
            "should",
            "yes",
        ),
        _rubric(
            "ctx.brand_safe",
            (
                "Is the image free of offensive, sexual, violent or culturally inappropriate "
                f"content (cultural notes: avoid {'; '.join(avoid)})?"
            ),
            "must",
            "yes",
        ),
    ]
    if season.holidays:
        checks.append(
            _rubric(
                "ctx.holiday_cue",
                f"Are there tasteful cues of {', '.join(season.holidays)}?",
                "should",
                "yes",
            )
        )
    return checks


# --- drafts ------------------------------------------------------------------------------------


def _clean(text: str, limit: int) -> str:
    return " ".join(_UNSAFE.sub(" ", text).split())[:limit].strip()


def sanitize_draft(draft: PlannerDraft, fallback: PlannerDraft) -> PlannerDraft:
    """Model cues end up in the image prompt: strip quote/markup characters, keep valid colours."""

    def cues(items: list[str], alt: list[str]) -> list[str]:
        cleaned = _dedup([_clean(i, 80) for i in items if _clean(i, 80)])[:5]
        return cleaned if len(cleaned) >= 2 else alt

    palette = [c.upper() for c in draft.palette if _HEX.match(c.strip())][:5]
    return PlannerDraft(
        setting=_clean(draft.setting, 160) or fallback.setting,
        locale_cues=cues(draft.locale_cues, fallback.locale_cues),
        season_cues=cues(draft.season_cues, fallback.season_cues),
        contradiction_cues=cues(draft.contradiction_cues, fallback.contradiction_cues),
        palette=palette if len(palette) >= 3 else fallback.palette,
        lighting=_clean(draft.lighting, 80) or fallback.lighting,
        mood=_clean(draft.mood, 40) or fallback.mood,
        cultural_avoid=[c for c in (_clean(i, 80) for i in draft.cultural_avoid) if c][:4],
        framing=as_framing(draft.framing),
        scale_references=_dedup([c for c in (_clean(i, 40) for i in draft.scale_references) if c])[
            :3
        ],
    )


def compose(
    aspect: Literal["1:1", "4:5"], zone: TextZone, draft: PlannerDraft, facts: ReferenceFacts | None
) -> Composition:
    """ADR-007: the product scale from its real-world size and the scene's framing (sizing.py),
    anchored away from the text zone (lower centre, or upper centre when the zone is at the
    bottom). Without a known size the pre-ADR-007 fixed scale (0.5 of the height) is kept."""
    anchor: Literal["lower_center", "upper_center"] = (
        "upper_center" if zone.anchor == "bottom" else "lower_center"
    )
    size = facts.size() if facts else None
    if size is None:
        return Composition(aspect_ratio=aspect, product_anchor=anchor, product_scale=0.5)
    scale = plan_scale(size.cm, as_framing(draft.framing))
    references = list(draft.scale_references) or list(DEFAULT_SCALE_REFERENCES[size.size_class])
    return Composition(
        aspect_ratio=aspect,
        product_anchor=anchor,
        product_scale=scale.mid,
        scale_min=scale.scale_min,
        scale_max=scale.scale_max,
        framing=scale.framing,
        size_cm=size.cm,
        size_class=size.size_class,
        size_source=size.source,
        resting_surface=size.surface,
        scale_references=references[:3],
        note=scale.note,
    )


def default_draft(geo: GeoResolution, season: SeasonResolution) -> PlannerDraft:
    table = cue_table()
    row = table.seasons[season.effective_season]
    holiday_cues: list[str] = []
    for hid in season.holiday_ids:
        hrow = holiday_row(hid)
        if hrow is not None:
            holiday_cues += hrow.cues[:1]
    locale = (
        geo.locale_cues if geo.known and len(geo.locale_cues) >= 2 else table.generic_locale_cues
    )
    return PlannerDraft(
        setting=row.setting,
        locale_cues=list(locale[:5]),
        season_cues=_dedup([*holiday_cues, *row.season_cues])[:5],
        contradiction_cues=list(row.contradiction_cues[:5]),
        palette=list(row.palette[:5]),
        lighting=row.lighting,
        mood=row.mood,
        cultural_avoid=[],
    )


def planner_facts(
    geo: GeoResolution, season: SeasonResolution, facts: ReferenceFacts | None
) -> dict[str, Any]:
    """Code-resolved, trusted facts only: no user-typed strings and never the required text."""
    return {
        "country": geo.country_name,
        "city": geo.city,
        "hemisphere": geo.hemisphere,
        "climate": geo.climate,
        "effective_season": season.effective_season,
        "months": season.months,
        "holidays": season.holidays,
        "product": (facts.category if facts and facts.category else "the product in the photo"),
        "product_colors": facts.dominant_colors[:4] if facts else [],
        **_size_facts(facts.size() if facts else None),
    }


def _size_facts(size: ProductSize | None) -> dict[str, Any]:
    if size is None:
        return {}
    return {
        "product_size_cm": round(size.cm),
        "size_class": size.size_class,
        "typical_surface": size.surface,
        "suggested_framing": default_framing(size.cm),
    }


def brief_hash(**fields: Any) -> str:
    canonical = json.dumps(fields, sort_keys=True, ensure_ascii=False, default=str)
    return hashlib.sha256(canonical.encode()).hexdigest()


class Planner:
    def __init__(self, gateway: LlmGateway | None) -> None:
        self.gateway = gateway

    # -- resolution (called at POST time so bad input is a 422 before any spend) --

    async def resolve(
        self, geography_code: str, geography_detail: str | None, season: str
    ) -> Resolution:
        geo = resolve_geography(geography_code, geography_detail)
        resolved = resolve_season(season, geo)
        if resolved is None:
            resolved = await self._model_season(season, geo)
        if resolved is None:
            raise unknown_season(season)
        return Resolution(geo, resolved)

    def _season_prompt(self) -> Prompt:
        ids = sorted(season_table().holidays)
        digest = hashlib.sha256(",".join(ids).encode()).hexdigest()[:8]
        return Prompt(
            name=SEASON_RESOLVE.name,
            version=f"{SEASON_RESOLVE.version}.{digest}",
            system=SEASON_RESOLVE.system.format(holiday_ids=", ".join(ids)),
        )

    async def _model_season(self, text: str, geo: GeoResolution) -> SeasonResolution | None:
        if self.gateway is None:
            return None
        try:
            clean = check_input(text, 40)
            result = await self.gateway.run(
                self._season_prompt(),
                user_input=clean,  # wrapped with wrap_untrusted by the gateway
                output_type=SeasonGuess,
                cacheable=True,
            )
        except Exception as exc:  # noqa: BLE001 - no model or a failed call means abstain
            log.info("season_model_unavailable", error_type=type(exc).__name__)
            return None
        guess = result.output
        if guess.confidence != "high":
            return None
        if guess.holiday_id is not None:
            row = holiday_row(guess.holiday_id)
            if row is None:
                return None
            return season_from_months(
                text,
                holiday_months(row, geo),
                geo,
                kind="holiday",
                holiday=(guess.holiday_id, row),
                source="model",
            )
        if guess.named_season is not None:
            return named_season(
                text, cast(EffectiveSeason, guess.named_season), geo, source="model"
            )
        months = [m for m in guess.months if 1 <= m <= 12]
        if not months or len(months) != len(guess.months) or len(months) > 6:
            return None
        return season_from_months(text, months, geo, kind="month", source="model")

    # -- planning --

    async def _draft(
        self, geo: GeoResolution, season: SeasonResolution, facts: ReferenceFacts | None
    ) -> tuple[PlannerDraft, Literal["llm", "default_table"], str | None, str | None]:
        fallback = default_draft(geo, season)
        if not geo.known:
            return fallback, "default_table", None, "conservative policy: no market data"
        if self.gateway is None:
            return fallback, "default_table", None, "no planner model configured"
        try:
            result = await self.gateway.run(
                CREATIVE_PLANNER,
                user_input=json.dumps(planner_facts(geo, season, facts), ensure_ascii=False),
                output_type=PlannerDraft,
                cacheable=True,
                wrap=False,  # code-produced facts, not user text
            )
        except Exception as exc:  # noqa: BLE001 - planner down -> deterministic cue table
            log.info("planner_fallback", error_type=type(exc).__name__)
            return fallback, "default_table", None, f"planner unavailable ({type(exc).__name__})"
        return sanitize_draft(result.output, fallback), "llm", result.model, None

    async def plan(
        self,
        *,
        resolution: Resolution,
        required_text: str,
        aspect_ratio: str,
        facts: ReferenceFacts | None = None,
        product_key: str = "",
    ) -> CreativeSpec:
        geo, season = resolution.geo, resolution.season
        draft, source, model, note = await self._draft(geo, season, facts)
        policy = compile_policy(geo, season.holiday_ids)
        aspect: Literal["1:1", "4:5"] = "1:1" if aspect_ratio == "1:1" else "4:5"
        zone = _text_zone(aspect, draft.palette, facts)
        return CreativeSpec(
            brief_hash=brief_hash(
                product=product_key,
                country=geo.country_code,
                city=geo.city,
                season=season.input,
                text=required_text,
                aspect=aspect,
            ),
            geo=geo,
            season=season,
            draft=draft,
            draft_source=source,
            planner_model=model,
            planner_note=note,
            composition=compose(aspect, zone, draft, facts),
            text_zone=zone,
            required_text=build_required_text(required_text),
            policy=policy,
            rubric=compile_rubric(geo, season, draft, policy),
            negatives=_dedup([*draft.contradiction_cues, *draft.cultural_avoid, *policy.avoid]),
        )
