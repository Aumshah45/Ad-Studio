"""`golden plant` (`make plant`): E-plant, the planted failures of ai-design §9.1.

Bases are E-nat images a human labelled all-pass (`labels.csv`), or every E-nat image with
`assume_pass` (dry runs only). Each pure item breaks exactly one dimension and inherits the base's
labels for the others, so its labels are certain by construction:

    text_typo 6 · text_missing 3 · text_stray 2 · product_recolour 4 · product_swap 3 ·
    product_duplicate 2 · product_logo_erased 2 · technical 4 · control_good 4 (no failure) ·
    comp_oversize 3 · comp_pasted 3 (ADR-007: bases also labelled composition pass, rubric v2)

plus 6 **generated** context failures: the brief's own spec with a contradicting forced scene
(context_season 4: summer <-> winter; context_geo 2: another country), made by the same generator,
prompt and image model as the pipeline, then judged against the brief's original rubric. Only
their context label is certain; the other labels come from a human check in `labels.csv`.

Choices (which bases, which edit, which hue) are drawn from `random.Random(seed:item)` when the
params are planned and stored in the manifest; the mutation itself is a pure function of the
source image and its params.
"""

import hashlib
import io
import random
from collections import Counter
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any, Literal

import numpy as np
from PIL import Image

from backend.domain.adstudio.adprompt import prompt_fields
from backend.domain.adstudio.evaluator.color import compare_colours
from backend.domain.adstudio.evaluator.config import load_config
from backend.domain.adstudio.generator import GeneratedImage, Generator
from backend.domain.adstudio.image_clients import ImageRequest
from backend.domain.adstudio.planner import ReferenceFacts, parse_facts
from backend.domain.adstudio.prompting import render_ad_prompt
from backend.domain.adstudio.spec import CreativeSpec
from backend.domain.adstudio.textnorm import compact
from backend.golden.context import GoldenContext
from backend.golden.dataset import (
    DIMENSIONS,
    GoldenPaths,
    LabelRow,
    OutputItem,
    PlantedItem,
    load_golden,
    read_labels,
    read_outputs,
    read_planted,
    write_jsonl,
)
from backend.golden.harness import product_loader, reference_images
from backend.golden.mutations import FAILS, apply_mutation, box_to_pixels, logo_mask, open_rgb
from backend.llm.prompts.ad_generate import AD_GENERATE
from backend.storage.blobs import sha256_hex

# plant-3 (analysis R2): the product classes leave composition and context unasserted (images
# unchanged from plant-2). plant-2: composition classes (ADR-007).
GENERATOR_VERSION = "plant-3"
PRODUCT_CLASSES = frozenset(
    {"product_recolour", "product_swap", "product_duplicate", "product_logo_erased"}
)
# A product edit is a digital composite with visible seams (a filled box behind a swapped product,
# recoloured background pixels inside the box, cut-out fringes): whether the edited product still
# looks photographed in the scene (composition), and whether the context rubric's product-hero /
# clean-ad questions still pass, is not certain by construction, so those labels are left
# unasserted (None) instead of inherited from the base. Only the broken dimension (product) and
# the untouched ones (technical, text) are asserted. Seamless blending was tried and rejected: a
# pure numpy/scikit-image product mask could not separate a white mug from a white wall or a dark
# tube from dark wood, and the inpainted erasures left new smears (see analysis R2 follow-up).
UNASSERTED: dict[str, tuple[str, ...]] = {m: ("composition", "context") for m in PRODUCT_CLASSES}
PURE_PLAN: tuple[tuple[str, int], ...] = (
    ("text_typo", 6),
    ("text_missing", 3),
    ("text_stray", 2),
    ("product_recolour", 4),
    ("product_swap", 3),
    ("product_duplicate", 2),
    ("product_logo_erased", 2),
    ("technical", 4),
    ("control_good", 4),
    ("comp_oversize", 3),
    ("comp_pasted", 3),
)
COMPOSITION_CLASSES = frozenset({"comp_oversize", "comp_pasted"})
OVERSIZE_SCALE = 1.8
# Below this the base can't carry the class (its product is already close to the zone). v1 bases
# (products at ~0.5 of the height) get ~1.4x; realistic v2 bases get the full 1.8x.
OVERSIZE_MIN = 1.35
GENERATED_PLAN: tuple[tuple[str, int], ...] = (("context_season", 4), ("context_geo", 2))
TECHNICAL_VARIANTS = ("upscale_2048", "wrong_aspect", "blank", "truncated_jpeg")
CONTROL_VARIANTS = ("identity", "jpeg_q80", "brightness", "jpeg_q80_brightness")
STRAY_WORDS = ("SALEE XQ", "BUYY NOWW ZK", "FREEE XQZ")
TYPO_EXAMPLES = (("30%", "38%"), ("$19.90", "$19.60"), ("Summer", "Sumer"))
# Products with a printed logo or label (SOURCES.md) when the vision profile is not available.
LABELLED_PRODUCTS = frozenset({"P1", "P2", "P5"})
# Briefs whose effective season or locale makes a contradiction unambiguous (ai-design §9.1).
SEASON_BRIEFS = ("B01", "B02", "B06", "B10", "B16", "B19", "B12", "B05")
GEO_BRIEFS = ("B04", "B13", "B08", "B14")
WHOLE = (0, 0, 1000, 1000)  # a whole-photo box for comparing two reference photos
WARM = frozenset({"summer", "hot", "tropical_wet", "tropical_dry"})


class PlantError(RuntimeError):
    pass


@dataclass
class PlantResult:
    items: list[PlantedItem] = field(default_factory=list[PlantedItem])
    skipped: list[str] = field(default_factory=list[str])
    bases: int = 0


def item_seed(seed: int, item_id: str) -> int:
    return int(hashlib.sha256(f"{seed}:{item_id}".encode()).hexdigest()[:8], 16)


# --- typos ----------------------------------------------------------------------------------------


def _other(ch: str, rng: random.Random) -> str:
    if ch.isdigit():
        return rng.choice([d for d in "0123456789" if d != ch])
    if ch.isascii() and ch.isalpha():
        pool = "abcdefghijklmnopqrstuvwxyz"
        new = rng.choice([c for c in pool if c != ch.lower()])
        return new.upper() if ch.isupper() else new
    return ch


def make_typo(text: str, script: str, rng: random.Random) -> tuple[str, dict[str, Any]]:
    """One visible edit (substitution, deletion or transposition) that survives normalisation
    (case and whitespace don't count as a typo). §9.1's examples are used when they apply."""
    examples = [(a, b) for a, b in TYPO_EXAMPLES if a in text]
    if examples and rng.random() < 0.75:
        a, b = rng.choice(examples)
        return text.replace(a, b, 1), {"kind": "example", "from": a, "to": b}
    positions = [i for i, ch in enumerate(text) if ch.isalnum()]
    for _ in range(200):
        kind = rng.choice(("substitution", "deletion", "transposition"))
        i = rng.choice(positions)
        if kind == "substitution":
            repl = _other(text[i], rng)
            if repl == text[i]:  # a non-Latin letter: swap in another letter of the text
                others = [c for c in text if c.isalnum() and c != text[i]]
                if not others:
                    continue
                repl = rng.choice(others)
            new = text[:i] + repl + text[i + 1 :]
        elif kind == "deletion":
            new = text[:i] + text[i + 1 :]
        else:
            if i + 1 >= len(text) or not text[i + 1].isalnum() or text[i] == text[i + 1]:
                continue
            new = text[:i] + text[i + 1] + text[i] + text[i + 2 :]
        if new.strip() and compact(new, script) != compact(text, script):
            return new, {"kind": kind, "position": i}
    raise PlantError(f"could not make a typo of {text!r}")


# --- param planning -------------------------------------------------------------------------------


@dataclass
class Base:
    item: OutputItem
    spec: CreativeSpec
    data: bytes

    @property
    def zone(self) -> list[float]:
        z = self.spec.text_zone.box
        return [z.x0, z.y0, z.x1, z.y1]

    @property
    def box(self) -> list[int] | None:
        return self.item.product_box


def _overlaps(a: tuple[float, float, float, float], b: tuple[float, float, float, float]) -> bool:
    return a[0] < b[2] and b[0] < a[2] and a[1] < b[3] and b[1] < a[3]


def _stray_spot(base: Base, rng: random.Random, size: float) -> tuple[float, float] | None:
    """A top-left point for the stray word that avoids the zone and the product box."""
    zx0, zy0, zx1, zy1 = base.zone
    avoid = [(zx0, zy0 - 0.02, zx1, zy1 + 0.02)]
    if base.box:
        ymin, xmin, ymax, xmax = base.box
        avoid.append(
            (xmin / 1000 - 0.02, ymin / 1000 - 0.02, xmax / 1000 + 0.02, ymax / 1000 + 0.02)
        )
    spots = [(x, y) for y in (0.30, 0.40, 0.50, 0.60, 0.72, 0.85) for x in (0.04, 0.55)]
    rng.shuffle(spots)
    for x, y in spots:
        rect = (x, y, x + 0.42, y + size * 1.3)
        if rect[2] <= 1 and rect[3] <= 1 and not any(_overlaps(rect, a) for a in avoid):
            return x, y
    return None


def _swap_partner(product_id: str, refs: Mapping[str, bytes]) -> tuple[str, float]:
    """The golden product whose colours differ most from this one's (CIEDE2000, evaluator
    settings), so the swap is a clear identity change."""
    cfg = load_config().product
    with Image.open(io.BytesIO(refs[product_id])) as img:
        own = img.convert("RGB")
    best: tuple[float, str] = (-1.0, "")
    for other, data in sorted(refs.items()):
        if other == product_id:
            continue
        with Image.open(io.BytesIO(data)) as img:
            cmp = compare_colours(own, None, img.convert("RGB"), WHOLE, cfg)
        score = max(cmp.delta_e, cmp.worst)
        if score > best[0]:
            best = (score, other)
    return best[1], round(best[0], 2)


def _has_label(base: Base) -> bool:
    facts = parse_facts(base.item.facts)
    if facts is not None and facts.category:
        return bool(facts.visible_text) or base.item.product_id in LABELLED_PRODUCTS
    return base.item.product_id in LABELLED_PRODUCTS


def plan_params(
    mutation: str,
    base: Base,
    rng: random.Random,
    index: int,
    *,
    partner: Callable[[str], tuple[str, float]],
) -> dict[str, Any] | None:
    """Params for one pure mutation of `base`, or None when this base can't carry it."""
    text = base.spec.required_text
    zone = base.zone
    if mutation == "text_typo":
        if text.mode != "native":
            return None
        typo, edit = make_typo(text.raw, text.script, rng)
        return {
            "zone": zone,
            "band_color": base.spec.text_zone.band_color,
            "max_lines": base.spec.text_zone.max_lines,
            "original": text.raw,
            "typo": typo,
            "edit": edit,
        }
    if mutation == "text_missing":
        if text.mode != "native":
            return None
        return {"zone": zone, "margin": 0.0, "delta": 30.0, "dilate": 3, "scale": 2}
    if mutation == "text_stray":
        size = 0.045
        spot = _stray_spot(base, rng, size)
        if spot is None:
            return None
        # Plain text in the colour that contrasts with what is behind it, like invented copy.
        img = open_rgb(base.data).convert("L")
        w, h = img.size
        x, y = spot
        region = img.crop(
            (round(x * w), round(y * h), round((x + 0.42) * w), round((y + size) * h))
        )
        dark_bg = float(np.asarray(region, dtype=np.float64).mean()) < 128
        return {
            "text": STRAY_WORDS[index % len(STRAY_WORDS)],
            "x": x,
            "y": y,
            "size": size,
            "fill": "#FFFFFF" if dark_bg else "#141414",
            "stroke_width": 0.0,
        }
    if mutation in PRODUCT_CLASSES:
        if base.box is None:
            return None
        common: dict[str, Any] = {"box": list(base.box), "border_fraction": 0.06, "delta": 12.0}
        if mutation == "product_recolour":
            return {**common, "hue_deg": rng.randrange(60, 181, 15)}
        if mutation == "product_swap":
            other, distance = partner(base.item.product_id)
            return {**common, "partner": other, "partner_delta_e": distance, "fill": 0.95}
        if mutation == "product_duplicate":
            centre = (base.box[1] + base.box[3]) / 2000
            return {
                **common,
                "scale": round(rng.uniform(0.5, 0.6), 2),
                "anchor": "right" if centre < 0.5 else "left",
            }
        if not _has_label(base):
            return None
        params = {"box": list(base.box), "delta": 25.0, "bg_delta": 12.0, "dilate": 2, "scale": 2}
        img = open_rgb(base.data)
        crop = img.crop(box_to_pixels(tuple(base.box), *img.size))  # type: ignore[arg-type]
        coverage = float(logo_mask(crop, params).mean())
        if not 0.01 <= coverage <= 0.6:
            return None
        return {**params, "coverage": round(coverage, 4)}
    if mutation in COMPOSITION_CLASSES:
        if base.box is None:
            return None
        ymin, xmin, ymax, xmax = base.box
        common = {"box": list(base.box), "border_fraction": 0.06, "delta": 12.0}
        if mutation == "comp_pasted":
            return {**common, "shadow": 0.04, "lift": 0.03, "halo_px": 4, "halo_alpha": 0.85}
        # Scale in place (anchored at the product's base), capped so the product stays below the
        # headline zone (1% margin) and inside the image: only composition breaks.
        zone_bottom = base.zone[3] * 1000 + 10
        room_up = (ymax - zone_bottom) / max(1, ymax - ymin)
        room_side = 960 / max(1, xmax - xmin)
        scale = round(min(OVERSIZE_SCALE, room_up, room_side), 2)
        if scale < OVERSIZE_MIN:
            return None
        return {**common, "scale": scale, "feather": 1.0}
    if mutation == "technical":
        return {"variant": TECHNICAL_VARIANTS[index % len(TECHNICAL_VARIANTS)], "keep": 0.6}
    if mutation == "control_good":
        return {
            "variant": CONTROL_VARIANTS[index % len(CONTROL_VARIANTS)],
            "factor": 1.08,
            "quality": 80,
        }
    raise PlantError(f"unknown mutation {mutation}")


def _labels_for(
    fails: Sequence[str], base: LabelRow | None = None, unasserted: Sequence[str] = ()
) -> dict[str, bool | None]:
    """Broken dimensions fail; the rest inherit the base's label (all-pass bases), so a rubric-1
    base leaves composition unlabelled (None) instead of claiming it passes. `unasserted`
    dimensions are left None (not certain by construction, plant-3)."""
    inherited = base.dims() if base is not None else {d: True for d in DIMENSIONS}
    return {
        d: False if d in fails else (None if d in unasserted else inherited.get(d))
        for d in DIMENSIONS
    }


def eligible_bases(
    paths: GoldenPaths, outputs: Sequence[OutputItem], *, assume_pass: bool
) -> list[OutputItem]:
    labels = read_labels(paths.labels)
    bases: list[OutputItem] = []
    for item in outputs:
        if item.set != "nat":
            continue
        row = labels.get((item.image_sha, "nat"))
        if row is not None:
            if row.complete and row.all_pass:
                bases.append(item)
        elif assume_pass:
            bases.append(item)
    return bases


async def plant_pure(
    paths: GoldenPaths, *, seed: int = 16, assume_pass: bool = False
) -> PlantResult:
    outputs = read_outputs(paths)
    if not outputs:
        raise PlantError(
            "outputs/manifest.jsonl is empty: run `make golden-run golden-export` first"
        )
    candidates = eligible_bases(paths, outputs, assume_pass=assume_pass)
    labels = read_labels(paths.labels)
    base_rows = {i.id: labels.get((i.image_sha, "nat")) for i in candidates}
    if not candidates:
        raise PlantError(
            "no E-nat image is labelled all-pass in labels.csv (label them first, or pass "
            "--assume-pass for a dry run)"
        )
    golden = load_golden(paths)
    refs = await reference_images(paths, golden)

    partners: dict[str, tuple[str, float]] = {}

    def partner(pid: str) -> tuple[str, float]:
        if pid not in partners:
            partners[pid] = _swap_partner(pid, refs)
        return partners[pid]

    bases = [
        Base(i, CreativeSpec.model_validate(i.spec), (paths.outputs / i.file).read_bytes())
        for i in candidates
    ]
    result = PlantResult(bases=len(bases))
    usage: Counter[str] = Counter()
    loader = product_loader(refs)
    n = 0
    for mutation, count in PURE_PLAN:
        rng = random.Random(item_seed(seed, mutation))  # noqa: S311 - seeded sampling, not crypto
        made = 0
        for index in range(count):
            order = sorted(bases, key=lambda b: (usage[b.item.id], rng.random()))
            if mutation == "text_typo":  # Latin copy first: typos there are unambiguous
                order.sort(key=lambda b: b.spec.required_text.script != "Latn")
            for base in order:
                row = base_rows.get(base.item.id)
                if (
                    mutation in COMPOSITION_CLASSES
                    and row is not None
                    and row.composition is not True
                ):
                    continue  # a composition failure needs a base a human passed on composition
                n_id = f"PL{n + 1:02d}-{mutation}"
                s = item_seed(seed, n_id)
                rng_item = random.Random(s)  # noqa: S311 - seeded sampling, not crypto
                params = plan_params(mutation, base, rng_item, index, partner=partner)
                if params is None:
                    continue
                data = apply_mutation(mutation, base.data, params, loader)
                fails = FAILS[mutation]
                result.items.append(
                    PlantedItem(
                        id=n_id,
                        mutation=mutation,
                        kind="pure",
                        source_id=base.item.id,
                        source_sha=base.item.image_sha,
                        brief_id=base.item.brief_id,
                        product_id=base.item.product_id,
                        params=params,
                        seed=s,
                        fails_dimensions=list(fails),
                        labels=_labels_for(fails, row, UNASSERTED.get(mutation, ())),
                        image_sha=sha256_hex(data),
                        generator_version=GENERATOR_VERSION,
                    )
                )
                usage[base.item.id] += 1
                n += 1
                made += 1
                break
        if made < count:
            result.skipped.append(f"{mutation}: {made}/{count} (not enough eligible bases)")
    return result


# --- generated context contradictions -------------------------------------------------------------

ForcedKind = Literal["context_season", "context_geo"]
WINTER_SCENE: dict[str, Any] = {
    "setting": "a snowy winter scene with falling snow and frosted pine trees",
    "season_cues": [
        "deep snow on the ground",
        "falling snowflakes",
        "people in heavy winter coats",
    ],
    "palette": ["#DDE7F0", "#9DB4C8", "#FFFFFF"],
    "lighting": "cold, overcast winter light",
    "mood": "wintry",
}
SUMMER_SCENE: dict[str, Any] = {
    "setting": "a sunny tropical beach in high summer with palm trees and surf",
    "season_cues": ["hot white sand", "bright midday sun", "people in swimwear"],
    "palette": ["#F4D35E", "#4FB0C6", "#FFE8A3"],
    "lighting": "harsh, bright midday summer sun",
    "mood": "summery",
}
GEO_SCENES: tuple[dict[str, Any], ...] = (
    {
        "country": "Japan",
        "city": "Tokyo",
        "setting": "a neon-lit street in Tokyo, Japan at night",
        "locale_cues": ["Tokyo street at night", "Japanese shopfronts", "a Shinkansen-style train"],
        "palette": ["#1B1B3A", "#E0457B", "#50C9CE"],
    },
    {
        "country": "United Kingdom",
        "city": "London",
        "setting": "a rainy London street with red double-decker buses",
        "locale_cues": ["red double-decker bus", "black London cab", "wet cobbles in the rain"],
        "palette": ["#5C6B73", "#C1121F", "#253237"],
    },
)


def contradicting_spec(
    spec: CreativeSpec, kind: ForcedKind, index: int
) -> tuple[CreativeSpec, dict[str, Any]]:
    """The brief's spec with a forced scene that contradicts its season or its locale."""
    draft = spec.draft
    if kind == "context_season":
        scene = WINTER_SCENE if spec.season.effective_season in WARM else SUMMER_SCENE
        forced = {**scene, "against": spec.season.effective_season}
        new_draft = draft.model_copy(
            update={
                "setting": scene["setting"],
                "season_cues": list(scene["season_cues"]),
                "palette": list(scene["palette"]),
                "lighting": scene["lighting"],
                "mood": scene["mood"],
            }
        )
        return spec.model_copy(update={"draft": new_draft, "negatives": []}), forced
    scene = GEO_SCENES[index % len(GEO_SCENES)]
    forced = {**scene, "against": spec.geo.country_name}
    new_draft = draft.model_copy(
        update={
            "setting": scene["setting"],
            "locale_cues": list(scene["locale_cues"]),
            "palette": list(scene["palette"]),
        }
    )
    geo = spec.geo.model_copy(update={"country_name": scene["country"], "city": scene["city"]})
    return spec.model_copy(update={"draft": new_draft, "geo": geo, "negatives": []}), forced


@dataclass
class Contradiction:
    image: GeneratedImage
    prompt: str
    forced: dict[str, Any]
    spec: CreativeSpec  # the forced spec that drove the prompt (the target keeps the original)


async def generate_contradiction(
    generator: Generator,
    spec: CreativeSpec,
    facts: ReferenceFacts | None,
    reference: bytes,
    kind: ForcedKind,
    index: int,
    *,
    model: str,
    salt: str,
) -> Contradiction:
    """One ad from the brief's spec with a contradicting forced scene: the pipeline's own prompt
    compiler (`ad_generate`), generator and candidate image model."""
    forced_spec, forced = contradicting_spec(spec, kind, index)
    prompt = render_ad_prompt(prompt_fields(forced_spec, facts))
    request = ImageRequest(
        prompt=prompt,
        images=(reference,),
        aspect_ratio=spec.aspect_ratio,
        scene=forced_spec.scene(),
        label="candidate",
    )
    image = await generator.generate(
        request,
        model=model,
        prompt_name=AD_GENERATE.name,
        prompt_version=AD_GENERATE.version,
        salt=salt,
    )
    return Contradiction(image, prompt, forced, forced_spec)


async def plant_generated(
    ctx: GoldenContext, paths: GoldenPaths, *, seed: int = 16, start: int = 0
) -> PlantResult:
    """The 6 generated context failures, through the pipeline's generator (image call cached,
    rate-limited and recorded in the ledger like any candidate)."""
    outputs = {i.id: i for i in read_outputs(paths) if i.set == "nat"}
    golden = load_golden(paths)
    refs = await reference_images(paths, golden)
    deps = ctx.deps
    generator = Generator(
        deps.image_client, deps.blobs, deps.runtime, timeout_s=ctx.settings.image_timeout_s
    )
    result = PlantResult()
    n = start
    paths.planted.mkdir(parents=True, exist_ok=True)
    for kind, count in GENERATED_PLAN:
        preferred = SEASON_BRIEFS if kind == "context_season" else GEO_BRIEFS
        chosen = [f"{b}-nat" for b in preferred if f"{b}-nat" in outputs][:count]
        if len(chosen) < count:
            result.skipped.append(f"{kind}: {len(chosen)}/{count} (briefs missing from outputs)")
        for index, source_id in enumerate(chosen):
            item = outputs[source_id]
            n_id = f"PL{n + 1:02d}-{kind}"
            made = await generate_contradiction(
                generator,
                CreativeSpec.model_validate(item.spec),
                parse_facts(item.facts),
                refs[item.product_id],
                kind,  # type: ignore[arg-type]
                index,
                model=ctx.settings.image_model_candidate,
                salt=f"plant:{seed}:{n_id}",
            )
            generated, prompt, forced = made.image, made.prompt, made.forced
            data = deps.blobs.read(generated.sha256)
            name = f"{n_id}.png"
            (paths.planted / name).write_bytes(data)
            result.items.append(
                PlantedItem(
                    id=n_id,
                    mutation=kind,
                    kind="generated",
                    source_id=source_id,
                    source_sha=item.image_sha,
                    brief_id=item.brief_id,
                    product_id=item.product_id,
                    params={
                        "forced": forced,
                        "prompt_sha": sha256_hex(prompt.encode()),
                        "prompt_version": f"{AD_GENERATE.name}@{AD_GENERATE.version}",
                        "model": generated.served_model,
                        "requested_model": generated.requested_model,
                        "cached": generated.cached,
                    },
                    seed=item_seed(seed, n_id),
                    fails_dimensions=["context"],
                    labels={
                        "technical": None,
                        "text": None,
                        "product": None,
                        "context": False,
                        "composition": None,
                    },
                    image_sha=generated.sha256,
                    file=name,
                    generator_version=GENERATOR_VERSION,
                    verified=None,
                )
            )
            n += 1
    return result


def keep_generated(paths: GoldenPaths, pure: Sequence[PlantedItem]) -> list[PlantedItem]:
    """The existing manifest's generated items, unchanged (their images, ids and human-verified
    label rows stay as they are) for a pure-only regeneration (`make plant KEEP_GENERATED=1`)."""
    kept = [i for i in read_planted(paths) if i.kind == "generated"]
    clash = {i.id for i in pure} & {i.id for i in kept}
    if clash:
        raise PlantError(f"regenerated pure items reuse generated ids: {sorted(clash)}")
    missing = [i.id for i in kept if not i.file or not (paths.planted / i.file).exists()]
    if missing:
        raise PlantError(f"generated items without their image file: {missing}")
    return kept


def write_planted(paths: GoldenPaths, items: Sequence[PlantedItem]) -> None:
    write_jsonl(paths.planted_manifest, items)
    keep = {i.file for i in items if i.file}
    for stale in paths.planted.glob("*.png"):
        if stale.name not in keep:
            stale.unlink()
