"""Planted failures (ai-design §9.1, slice 16): each class is flagged on the expected dimension and
known-good controls pass. Fixtures are synthetic ads built in-test from the golden product photos
and golden brief specs; OCR is the local Tesseract (skipped when missing) and the judge is the fake
vision client, scripted only where the class is judged by the VLM (duplicates, context). No
network, no key."""

import io
from pathlib import Path
from random import Random

import numpy as np
import pytest
from PIL import Image

from backend.domain.adstudio.evaluator.schemas import Evaluation
from backend.domain.adstudio.generator import Generator
from backend.domain.adstudio.image_clients import FakeImageClient
from backend.domain.adstudio.textnorm import compact
from backend.domain.adstudio.vision import FakeVisionClient
from backend.golden.dataset import OutputItem, RunInfo
from backend.golden.mutations import FAILS, apply_mutation, box_to_pixels
from backend.golden.plant import (
    PRODUCT_CLASSES,
    UNASSERTED,
    Base,
    _swap_partner,
    generate_contradiction,
    make_typo,
    plan_params,
)
from backend.llm.cache import MemoryCache
from backend.llm.calls import CallRuntime
from backend.storage.blobs import BlobStore, sha256_hex
from tests.golden_fixtures import GoldenAd, evaluator, golden_ad, products, tesseract_available

needs_tesseract = pytest.mark.skipif(
    not tesseract_available(), reason="tesseract binary not found on PATH (live-OCR test)"
)
CANDIDATE_MODEL = "google:gemini-3.1-flash-lite-image"


def base_of(ad: GoldenAd, brief_id: str = "B01") -> Base:
    item = OutputItem(
        id=f"{brief_id}-nat",
        set="nat",
        brief_id=brief_id,
        product_id=ad.pid,
        golden_key=f"{brief_id}@test@fake",
        file=f"{brief_id}-nat.png",
        image_sha=sha256_hex(ad.data),
        width=819,
        height=1024,
        reference_sha=sha256_hex(ad.reference),
        spec=ad.spec.model_dump(mode="json"),
        candidate_kind="initial",
        attempt=0,
        slot=0,
        candidate_status="passed",
        image_client="fake",
        pipeline_version="test",
        product_box=list(ad.box),
        run=RunInfo(run_id="r", status="passed"),
    )
    return Base(item, ad.spec, ad.data)


async def plant(mutation: str, ad: GoldenAd, *, seed: int = 7, index: int = 0) -> bytes:
    refs = await products()
    params = plan_params(
        mutation,
        base_of(ad),
        Random(seed),  # noqa: S311
        index,
        partner=lambda pid: _swap_partner(pid, refs),
    )
    assert params is not None, f"{mutation} could not be planned on this fixture"
    return apply_mutation(mutation, ad.data, params, refs.__getitem__)


async def evaluate(ad: GoldenAd, data: bytes, vision: FakeVisionClient | None = None) -> Evaluation:
    return await evaluator(ad, vision).evaluate(data, ad.reference, ad.target)


def dims(ev: Evaluation) -> dict[str, bool | None]:
    return {d: r.passed for d, r in ev.dimensions.items()}


@needs_tesseract
async def test_fixture_ad_passes_before_mutation() -> None:
    ad = await golden_ad()
    assert dims(await evaluate(ad, ad.data)) == dict.fromkeys(
        ("technical", "text", "product", "context", "composition"), True
    )


@needs_tesseract
@pytest.mark.parametrize("seed", [0, 1, 2, 3])
async def test_evaluator_flags_text_typos(seed: int) -> None:
    ad = await golden_ad()
    data = await plant("text_typo", ad, seed=seed)
    ev = await evaluate(ad, data)
    assert ev.dimensions["text"].passed is False
    assert ev.dimensions["product"].passed is True  # exactly one dimension broken
    assert "ocr_cer" in ev.dimensions["text"].failed_checks


def test_typo_edits_survive_normalisation() -> None:
    for seed in range(40):
        for text, script in (
            ("Summer Sale — 30% OFF", "Latn"),
            ("Holiday Deals from $19.90", "Latn"),
            ("春の新作", "Jpan"),
            ("दिवाली सेल 20% छूट", "Deva"),
        ):
            typo, edit = make_typo(text, script, Random(seed))  # noqa: S311
            assert compact(typo, script) != compact(text, script), (text, edit)


@needs_tesseract
async def test_evaluator_flags_text_missing() -> None:
    ad = await golden_ad()
    ev = await evaluate(ad, await plant("text_missing", ad))
    assert ev.dimensions["text"].passed is False
    assert ev.dimensions["product"].passed is True


@needs_tesseract
@pytest.mark.parametrize("index", [0, 1])
async def test_evaluator_flags_stray_text(index: int) -> None:
    ad = await golden_ad()
    ev = await evaluate(ad, await plant("text_stray", ad, index=index))
    assert ev.dimensions["text"].passed is False
    assert "stray_text" in ev.dimensions["text"].failed_checks
    assert ev.dimensions["product"].passed is True


@needs_tesseract
@pytest.mark.parametrize("seed", [0, 1, 2])
async def test_evaluator_flags_recoloured_product(seed: int) -> None:
    ad = await golden_ad()
    ev = await evaluate(ad, await plant("product_recolour", ad, seed=seed))
    assert ev.dimensions["product"].passed is False
    assert "color_delta_e" in ev.dimensions["product"].failed_checks
    assert ev.dimensions["text"].passed is True


@needs_tesseract
async def test_evaluator_flags_swapped_product() -> None:
    ad = await golden_ad()
    refs = await products()
    partner, distance = _swap_partner("P1", refs)
    assert partner != "P1" and distance > 18  # the most dissimilar golden product
    ev = await evaluate(ad, await plant("product_swap", ad))
    assert ev.dimensions["product"].passed is False
    assert ev.dimensions["text"].passed is True


def _correlation(a: Image.Image, b: Image.Image) -> float:
    x = np.asarray(a.convert("L").resize((48, 48)), dtype=np.float64).ravel()
    y = np.asarray(b.convert("L").resize((48, 48)), dtype=np.float64).ravel()
    return float(np.corrcoef(x, y)[0, 1])


@needs_tesseract
async def test_evaluator_flags_duplicated_product() -> None:
    """The mutation really adds a second copy (checked on the pixels); the evaluator's product
    gate then fails on the judge's instance count (ai-design §5.3: boxes for every instance)."""
    ad = await golden_ad()
    data = await plant("product_duplicate", ad)
    after = np.asarray(Image.open(io.BytesIO(data)).convert("RGB"), dtype=np.int16)
    before_img = Image.open(io.BytesIO(ad.data)).convert("RGB")
    before = np.asarray(before_img, dtype=np.int16)
    changed = np.abs(after - before).sum(axis=2) > 30
    ys, xs = np.nonzero(changed)
    assert len(xs) > 0
    copy_box = (int(xs.min()), int(ys.min()), int(xs.max()) + 1, int(ys.max()) + 1)
    left, top, right, bottom = box_to_pixels(ad.box, before_img.width, before_img.height)
    overlap_w = max(0, min(copy_box[2], right) - max(copy_box[0], left))
    assert overlap_w < 0.25 * (copy_box[2] - copy_box[0])  # mostly beside the hero, not on it
    copy = Image.fromarray(
        after[copy_box[1] : copy_box[3], copy_box[0] : copy_box[2]].astype(np.uint8)
    )
    assert _correlation(copy, before_img.crop((left, top, right, bottom))) > 0.3
    judge = FakeVisionClient(box=ad.box, product_count=2)
    ev = await evaluate(ad, data, judge)
    assert ev.dimensions["product"].passed is False
    assert "product_count" in ev.dimensions["product"].failed_checks
    clean = await evaluate(ad, ad.data, FakeVisionClient(box=ad.box, product_count=1))
    assert clean.dimensions["product"].passed is True


@needs_tesseract
async def test_evaluator_flags_erased_logo() -> None:
    ad = await golden_ad("P1")  # the mug's red logo panel
    ev = await evaluate(ad, await plant("product_logo_erased", ad))
    assert ev.dimensions["product"].passed is False
    assert ev.dimensions["text"].passed is True


@pytest.mark.parametrize("index", [0, 1, 2, 3])
async def test_evaluator_flags_technical(index: int) -> None:
    ad = await golden_ad()
    data = await plant("technical", ad, index=index)
    ev = await evaluate(ad, data)
    assert ev.dimensions["technical"].passed is False
    assert ev.verdict == "fail"
    assert set(ev.dimensions) == {"technical"}  # the rest is not judged on a broken image


async def _contradiction(ad: GoldenAd, kind: str, tmp_path: Path) -> tuple[bytes, str]:
    client = FakeImageClient()
    generator = Generator(client, BlobStore(tmp_path), CallRuntime(cache=MemoryCache()))
    made = await generate_contradiction(
        generator,
        ad.spec,
        None,
        ad.reference,
        kind,  # type: ignore[arg-type]
        0,
        model=CANDIDATE_MODEL,
        salt="test",
    )
    assert client.calls and client.calls[0].prompt == made.prompt
    return BlobStore(tmp_path).read(made.image.sha256), made.prompt


@needs_tesseract
async def test_evaluator_flags_season_contradiction(tmp_path: Path) -> None:
    ad = await golden_ad()  # Australia / December -> summer
    data, prompt = await _contradiction(ad, "context_season", tmp_path)
    assert "snow" in prompt.lower()  # the forced scene contradicts the effective season
    rubric = {c.id for c in ad.target.context_checks}
    assert "ctx.no_season_contradiction" in rubric  # judged against the brief's own rubric
    judge = FakeVisionClient(
        box=ad.box, context={"ctx.no_season_contradiction": "yes", "ctx.season_cues": "no"}
    )
    ev = await evaluate(ad, data, judge)
    assert ev.dimensions["context"].passed is False
    assert "ctx.no_season_contradiction" in ev.dimensions["context"].failed_checks


@needs_tesseract
async def test_evaluator_flags_geo_contradiction(tmp_path: Path) -> None:
    ad = await golden_ad("P1", "BR", "summer", "Frete Grátis")
    data, prompt = await _contradiction(ad, "context_geo", tmp_path)
    assert "Tokyo" in prompt and "Brazil" not in prompt
    judge = FakeVisionClient(
        box=ad.box, context={"ctx.no_geo_contradiction": "yes", "ctx.geo_plausible": "no"}
    )
    ev = await evaluate(ad, data, judge)
    assert ev.dimensions["context"].passed is False
    assert {"ctx.no_geo_contradiction", "ctx.geo_plausible"} & set(
        ev.dimensions["context"].failed_checks
    )


def _scaled_box(box: tuple[int, int, int, int], scale: float) -> tuple[int, int, int, int]:
    ymin, xmin, ymax, xmax = box
    h, w = (ymax - ymin) * scale, (xmax - xmin) * scale
    cx = (xmin + xmax) / 2
    return (round(ymax - h), round(cx - w / 2), ymax, round(cx + w / 2))


@needs_tesseract
async def test_evaluator_flags_oversized_product() -> None:
    """comp_oversize: the product scaled ~1.8x in place (clear of the headline zone). The judge is
    scripted (like the other VLM classes): composition fails, text and product still pass."""
    from backend.domain.adstudio.evaluator.composition import SCALE_CHECK

    ad = await golden_ad(product_scale=0.3)  # a realistically sized mug, as in v2
    refs = await products()
    params = plan_params(
        "comp_oversize",
        base_of(ad),
        Random(0),  # noqa: S311
        0,
        partner=lambda pid: _swap_partner(pid, refs),
    )
    assert params is not None and params["scale"] == 1.8
    data = apply_mutation("comp_oversize", ad.data, params, refs.__getitem__)
    big = _scaled_box(ad.box, float(params["scale"]))
    assert big[0] > ad.zone[3] * 1000  # still below the headline zone
    judge = FakeVisionClient(box=big, composition={SCALE_CHECK: "no"})
    ev = await evaluate(ad, data, judge)
    assert ev.dimensions["composition"].passed is False
    assert ev.dimensions["composition"].failed_checks == [SCALE_CHECK]
    assert ev.dimensions["text"].passed is True and ev.dimensions["product"].passed is True
    # The product really is bigger: its pixels now reach well above the original box.
    img = np.asarray(Image.open(io.BytesIO(data)).convert("RGB"), dtype=np.int16)
    orig = np.asarray(Image.open(io.BytesIO(ad.data)).convert("RGB"), dtype=np.int16)
    h = img.shape[0]
    band = slice(round(big[0] / 1000 * h), round(ad.box[0] / 1000 * h))
    assert float(np.abs(img[band] - orig[band]).mean()) > 10


@needs_tesseract
async def test_evaluator_flags_pasted_product() -> None:
    """comp_pasted: cut out, shadow erased, lifted, hard edge with a light halo; the scripted judge
    fails natural integration; text and product still pass."""
    from backend.domain.adstudio.evaluator.composition import INTEGRATION_CHECK

    ad = await golden_ad()
    data = await plant("comp_pasted", ad)
    judge = FakeVisionClient(box=ad.box, composition={INTEGRATION_CHECK: "no"})
    ev = await evaluate(ad, data, judge)
    assert ev.dimensions["composition"].passed is False
    assert ev.dimensions["composition"].failed_checks == [INTEGRATION_CHECK]
    assert ev.dimensions["text"].passed is True and ev.dimensions["product"].passed is True
    assert sha256_hex(data) != sha256_hex(ad.data)


def test_composition_classes_need_a_composition_labelled_base() -> None:
    from backend.golden.dataset import LabelRow
    from backend.golden.plant import _labels_for

    rubric1 = LabelRow(
        image_sha="a",
        set="nat",
        brief_id="B01",
        technical=True,
        text=True,
        product=True,
        context=True,
        rubric_version="1",
    )
    assert rubric1.complete and rubric1.all_pass and rubric1.composition is None
    labels = _labels_for(["text"], rubric1)
    assert labels["text"] is False and labels["product"] is True
    assert labels["composition"] is None  # never claimed from a base that wasn't labelled on it
    assert _labels_for(["composition"])["composition"] is False


@needs_tesseract
@pytest.mark.parametrize("index", [0, 1, 2, 3])
async def test_passes_known_good(index: int) -> None:
    ad = await golden_ad()
    ev = await evaluate(ad, await plant("control_good", ad, index=index))
    assert ev.verdict == "pass", {d: r.reasons for d, r in ev.dimensions.items()}


async def test_pure_mutations_are_reproducible_from_params() -> None:
    ad = await golden_ad()
    refs = await products()
    for mutation in FAILS:
        if mutation.startswith("context_"):
            continue
        params = plan_params(
            mutation,
            base_of(ad),
            Random(3),  # noqa: S311
            0,
            partner=lambda pid: _swap_partner(pid, refs),
        )
        assert params is not None, mutation
        first = apply_mutation(mutation, ad.data, params, refs.__getitem__)
        again = apply_mutation(mutation, ad.data, params, refs.__getitem__)
        assert sha256_hex(first) == sha256_hex(again), mutation


async def test_make_plant_builds_every_class_from_labelled_bases(tmp_path: Path) -> None:
    """`plant_pure` on a small dataset: only bases a human labelled all-pass are used, every pure
    class gets its count, labels are set by construction and each item regenerates exactly."""
    from backend.golden.dataset import GOLDEN_DIR, GoldenPaths, LabelRow, write_jsonl, write_labels
    from backend.golden.harness import materialize
    from backend.golden.plant import PURE_PLAN, plant_pure

    paths = GoldenPaths(source=GOLDEN_DIR, out=tmp_path)
    briefs = {
        "B01": ("P1", "AU", "December", "Summer Sale — 30% OFF"),
        "B05": ("P2", "DE", "October", "Oktoberfest Edition"),
        "B10": ("P3", "ZA", "June", "Winter Run Club"),
        "B14": ("P4", "KE", "April", "Hydrate Anywhere"),
        "B18": ("P5", "FR", "May", "Nouveau: Éclat 24h"),
        "B02": ("P1", "CA", "December", "Winter Warmers"),
    }
    items: list[OutputItem] = []
    labels: list[LabelRow] = []
    paths.outputs.mkdir(parents=True)
    for brief_id, (pid, code, season, text) in briefs.items():
        ad = await golden_ad(pid, code, season, text)
        item = base_of(ad, brief_id).item
        (paths.outputs / item.file).write_bytes(ad.data)
        items.append(item)
        failing = brief_id == "B02"  # a human said its text fails: never a base
        labels.append(
            LabelRow(
                image_sha=item.image_sha,
                set="nat",
                brief_id=brief_id,
                technical=True,
                text=not failing,
                product=True,
                context=True,
                composition=True,
            )
        )
    write_jsonl(paths.output_manifest, items)
    write_labels(paths.labels, labels)

    result = await plant_pure(paths, seed=11)

    assert result.bases == 5 and not result.skipped
    counts: dict[str, int] = {}
    for item in result.items:
        counts[item.mutation] = counts.get(item.mutation, 0) + 1
        assert item.source_id != "B02-nat"
        unasserted = UNASSERTED.get(item.mutation, ())
        assert item.labels == {
            d: None if d in unasserted else d not in item.fails_dimensions for d in item.labels
        }
        assert item.fails_dimensions == FAILS[item.mutation]
        # plant-3 (R2): product edits leave composition and context unasserted.
        if item.mutation in PRODUCT_CLASSES:
            assert unasserted == ("composition", "context")
    assert counts == dict(PURE_PLAN)
    refs = await products()
    for item in result.items:
        assert sha256_hex(materialize(item, paths, refs)) == item.image_sha, item.id
