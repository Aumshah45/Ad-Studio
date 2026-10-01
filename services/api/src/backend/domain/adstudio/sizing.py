"""Real-world product size -> scene framing -> product scale (ADR-007, ai-design §2.2).

Deterministic code, no model. The product profile gives an approximate largest dimension in cm
(and a size class); the planner chooses a framing (close-up tabletop, medium, wide) and names a few
everyday scale-reference objects for the scene. This module turns those into the expected product
scale, i.e. the product's longest side as a fraction of the image height, so a 15 cm sunscreen
tube is never drawn taller than a chair.

    scale = size_cm / visible_scene_height_cm(framing)

The visible scene height of a framing is a range (a close-up tabletop shot shows roughly 35-55 cm
of the scene from top to bottom), so the scale is a range too. It is clamped to what still reads as
a hero product (`HERO_MIN`..`HERO_MAX`); when the chosen framing can't keep the product inside that
band, the nearest framing that can is used instead (a mug in a wide room shot would be a speck).
"""

import re
from dataclasses import dataclass
from typing import Literal, get_args

SizeClass = Literal["tiny", "small", "medium", "large", "xlarge"]
Framing = Literal["close_up", "medium", "wide"]
SIZE_CLASSES: tuple[SizeClass, ...] = get_args(SizeClass)
FRAMINGS: tuple[Framing, ...] = get_args(Framing)

# Upper bound (exclusive) of each class's largest dimension, cm.
SIZE_CLASS_MAX_CM: dict[SizeClass, float] = {
    "tiny": 8.0,
    "small": 20.0,
    "medium": 45.0,
    "large": 120.0,
    "xlarge": float("inf"),
}
# A representative size when only the class is known.
SIZE_CLASS_TYPICAL_CM: dict[SizeClass, float] = {
    "tiny": 5.0,
    "small": 13.0,
    "medium": 28.0,
    "large": 70.0,
    "xlarge": 180.0,
}
# How much of the scene (cm, top to bottom of the frame at the product's distance) each framing
# shows. Close-up tabletop: a camera ~40 cm away; medium: a table top with its surroundings;
# wide: a room or an outdoor scene with furniture and people.
FRAME_HEIGHT_CM: dict[Framing, tuple[float, float]] = {
    "close_up": (35.0, 55.0),
    "medium": (80.0, 120.0),
    "wide": (180.0, 300.0),
}
FRAMING_TEXT: dict[Framing, str] = {
    "close_up": "a close-up tabletop shot (camera about 40 cm away; the frame shows roughly "
    "35-55 cm of the scene from top to bottom)",
    "medium": "a medium shot (the frame shows roughly 80-120 cm of the scene from top to "
    "bottom, e.g. a table top with its surroundings)",
    "wide": "a wide shot (the frame shows roughly 2-3 m of the scene from top to bottom, e.g. a "
    "room or an outdoor setting)",
}
# The product must still read as the hero: its longest side between these fractions of the image
# height (the text zone takes the top ~20%, so 0.6 is the most that fits below it).
HERO_MIN = 0.18
HERO_MAX = 0.6
# Everyday objects with well-known sizes, used when the planner names none.
DEFAULT_SCALE_REFERENCES: dict[SizeClass, tuple[str, ...]] = {
    "tiny": ("a smartphone", "a coffee cup"),
    "small": ("a coffee cup", "a smartphone"),
    "medium": ("a coffee cup", "a hardback book"),
    "large": ("a chair", "a potted plant"),
    "xlarge": ("a door", "a chair"),
}
DEFAULT_SURFACE: dict[SizeClass, str] = {
    "tiny": "a table top",
    "small": "a table top",
    "medium": "a table top",
    "large": "the floor",
    "xlarge": "the floor",
}
# Category keywords -> typical largest dimension (cm): the fallback when a profile has no size
# (profiles made before pp-2, or a judge that left the field empty). First match wins.
CATEGORY_SIZES_CM: tuple[tuple[str, float], ...] = (
    ("lipstick", 8.0),
    ("lip balm", 7.0),
    ("ring", 2.5),
    ("earring", 3.0),
    ("watch", 4.5),
    ("perfume", 12.0),
    ("sunscreen", 15.0),
    ("tube", 15.0),
    ("lotion", 18.0),
    ("mug", 10.0),
    ("cup", 9.0),
    ("jar", 12.0),
    ("can", 12.0),
    ("smartphone", 15.0),
    ("phone", 15.0),
    ("sneaker", 28.0),
    ("shoe", 28.0),
    ("trainer", 28.0),
    ("boot", 30.0),
    ("water bottle", 25.0),
    ("wine", 30.0),
    ("bottle", 22.0),
    ("headphone", 20.0),
    ("handbag", 30.0),
    ("bag", 35.0),
    ("backpack", 45.0),
    ("laptop", 35.0),
    ("chair", 90.0),
    ("bicycle", 170.0),
    ("sofa", 200.0),
)


def size_class_for(cm: float) -> SizeClass:
    for cls in SIZE_CLASSES:
        if cm < SIZE_CLASS_MAX_CM[cls]:
            return cls
    return "xlarge"  # pragma: no cover - inf bound


def size_from_category(category: str | None) -> float | None:
    if not category:
        return None
    text = category.casefold()
    for keyword, cm in CATEGORY_SIZES_CM:
        if re.search(rf"\b{re.escape(keyword)}", text):
            return cm
    return None


def as_framing(value: str | None) -> Framing | None:
    if value is None:
        return None
    v = value.strip().lower().replace("-", "_").replace(" ", "_")
    if v in ("closeup", "close", "tabletop", "close_up_tabletop"):
        v = "close_up"
    return v if v in FRAMINGS else None


@dataclass(frozen=True)
class ProductSize:
    """What sizing knows about the product: its largest dimension and where it usually rests."""

    cm: float
    size_class: SizeClass
    surface: str
    source: Literal["profile", "category", "class"]


def resolve_size(
    *,
    cm: float | None,
    size_class: str | None,
    surface: str | None,
    category: str | None,
) -> ProductSize | None:
    """The profile's size (class recomputed from cm, so the two never disagree), else the class's
    typical size, else the category keyword table; None when nothing is known."""
    cls = size_class if size_class in SIZE_CLASSES else None
    if cm is not None and 0.5 <= cm <= 1000:
        known = size_class_for(cm)
        return ProductSize(cm, known, (surface or "").strip() or DEFAULT_SURFACE[known], "profile")
    if cls is not None:
        typical = SIZE_CLASS_TYPICAL_CM[cls]
        return ProductSize(typical, cls, (surface or "").strip() or DEFAULT_SURFACE[cls], "class")
    guess = size_from_category(category)
    if guess is None:
        return None
    known = size_class_for(guess)
    return ProductSize(guess, known, (surface or "").strip() or DEFAULT_SURFACE[known], "category")


def raw_scale_range(cm: float, framing: Framing) -> tuple[float, float]:
    lo_cm, hi_cm = FRAME_HEIGHT_CM[framing]
    return cm / hi_cm, cm / lo_cm


def default_framing(cm: float) -> Framing:
    """The tightest framing that keeps the product within the hero band (a close-up if the
    product fits one; wider for big products)."""
    for framing in FRAMINGS:
        lo, _ = raw_scale_range(cm, framing)
        if lo <= HERO_MAX:
            return framing
    return "wide"


def _fits(cm: float, framing: Framing) -> bool:
    lo, hi = raw_scale_range(cm, framing)
    return lo <= HERO_MAX and hi >= HERO_MIN


@dataclass(frozen=True)
class ScalePlan:
    framing: Framing
    scale_min: float
    scale_max: float
    note: str | None = None  # why the planner's framing was replaced, if it was

    @property
    def mid(self) -> float:
        return round((self.scale_min + self.scale_max) / 2, 3)


def plan_scale(cm: float, requested: Framing | None) -> ScalePlan:
    """Framing (the planner's when it fits the hero band, else the default) and the product scale
    range for it, clamped to [HERO_MIN, HERO_MAX]."""
    note = None
    framing = default_framing(cm)
    if requested is not None:
        if _fits(cm, requested) or requested == framing:
            framing = requested
        else:
            note = (
                f"framing {requested} can't show a {cm:.0f} cm product as the hero; used {framing}"
            )
    lo, hi = raw_scale_range(cm, framing)
    lo, hi = max(HERO_MIN, min(lo, HERO_MAX)), max(HERO_MIN, min(hi, HERO_MAX))
    if hi - lo < 0.04:  # clamped to a point: give the model a little room
        if lo <= HERO_MIN:
            hi = lo + 0.06
        else:
            lo = hi - 0.06
    return ScalePlan(framing, round(lo, 3), round(hi, 3), note)


def expected_area_range(
    scale_min: float, scale_max: float, *, aspect: float, product_aspect: float
) -> tuple[float, float]:
    """Expected product-box area as a fraction of the image, from the scale range (longest side /
    image height), the image aspect (w/h) and the product's short/long side ratio (reference box).
    """
    r = max(0.05, min(1.0, product_aspect))
    lo = scale_min * scale_min * r / aspect
    hi = scale_max * scale_max * r / aspect
    return lo, hi
