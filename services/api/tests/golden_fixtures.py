"""Synthetic golden ads for evaluator tests: a real golden product photo composited by the fake
image client into the scene of a real golden brief's spec (no network, no key)."""

import io
from dataclasses import dataclass

from PIL import Image

from backend.domain.adstudio.evaluator.core import EvalTarget, Evaluator
from backend.domain.adstudio.evaluator.ocr import TesseractOcr
from backend.domain.adstudio.image_clients import (
    FAKE_NATIVE_SIZES,
    fake_product_box,
    render_fake_ad,
)
from backend.domain.adstudio.imaging import postprocess_generated
from backend.domain.adstudio.pipeline import eval_target
from backend.domain.adstudio.planner import Planner
from backend.domain.adstudio.spec import CreativeSpec
from backend.domain.adstudio.uploads import MAX_UPLOAD_BYTES, normalize_upload
from backend.domain.adstudio.vision import FakeVisionClient
from backend.golden.dataset import GOLDEN_DIR
from backend.llm.cache import MemoryCache
from backend.llm.calls import CallRuntime

_refs: dict[str, bytes] = {}


async def reference(pid: str) -> bytes:
    """The normalised golden product photo, exactly as an upload stores it."""
    if pid not in _refs:
        raw = (GOLDEN_DIR / "products" / f"{pid}.jpg").read_bytes()
        _refs[pid] = (await normalize_upload(raw, max_bytes=MAX_UPLOAD_BYTES)).data
    return _refs[pid]


async def products() -> dict[str, bytes]:
    return {pid: await reference(pid) for pid in ("P1", "P2", "P3", "P4", "P5")}


async def spec_for(code: str, season: str, text: str) -> CreativeSpec:
    planner = Planner(None)  # default cue table: no model call
    resolution = await planner.resolve(code, None, season)
    return await planner.plan(resolution=resolution, required_text=text, aspect_ratio="4:5")


@dataclass
class GoldenAd:
    pid: str
    reference: bytes
    spec: CreativeSpec
    data: bytes
    box: tuple[int, int, int, int]  # where the product is, 0-1000 [ymin, xmin, ymax, xmax]

    @property
    def target(self) -> EvalTarget:
        return eval_target(self.spec)

    @property
    def zone(self) -> list[float]:
        z = self.spec.text_zone.box
        return [z.x0, z.y0, z.x1, z.y1]


async def golden_ad(
    pid: str = "P1",
    code: str = "AU",
    season: str = "December",
    text: str = "Summer Sale — 30% OFF",
    *,
    product_scale: float | None = None,
) -> GoldenAd:
    ref = await reference(pid)
    spec = await spec_for(code, season, text)
    if product_scale is not None:  # e.g. a realistically sized product (ADR-007)
        composition = spec.composition.model_copy(update={"product_scale": product_scale})
        spec = spec.model_copy(update={"composition": composition})
    img = render_fake_ad(ref, "4:5", spec.scene(), seed=f"golden-fixture:{pid}")
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=92)
    data = postprocess_generated(buf.getvalue()).data
    with Image.open(io.BytesIO(ref)) as r:
        size = r.size
    nw, nh = FAKE_NATIVE_SIZES["4:5"]
    x0, y0, x1, y1 = fake_product_box(size, (nw, nh), spec.composition.product_scale)
    box = (y0 * 1000 // nh, x0 * 1000 // nw, y1 * 1000 // nh, x1 * 1000 // nw)
    return GoldenAd(pid, ref, spec, data, box)


def evaluator(ad: GoldenAd, vision: FakeVisionClient | None = None) -> Evaluator:
    """Local Tesseract (cached in memory) and a fake judge that reports the product where it is."""
    return Evaluator(
        TesseractOcr(CallRuntime(cache=MemoryCache())),
        vision=vision or FakeVisionClient(box=ad.box),
    )


def tesseract_available() -> bool:
    return TesseractOcr().available()
