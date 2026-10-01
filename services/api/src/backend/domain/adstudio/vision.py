"""Vision judge (ai-design §5.3-5.4, Appendix A.1/A.8/A.9) behind one protocol.

- `GatewayVisionClient`: `LlmGateway.run_vision` on `VISION_JUDGE` (Gemini, temperature 0, cached by
  image sha256s + prompt version + evaluator version). With a `FileCache` runtime it replays a
  snapshot for evals, and a miss raises instead of calling the model.
- `FakeVisionClient`: scripted answers, no key, no network (tests and key-less dev,
  `VISION_CLIENT=fake`). By default it locates the product the way the fake image client placed it
  and answers every check favourably; tests override any answer, or make it raise (judge down).
- `NoVisionClient`: nothing configured; the evaluator marks vision checks `unverified`.

Judge authority is **veto, not pardon**: the evaluator lets these answers add failures to text and
product, never remove a deterministic one. Label text read from images is data, never instructions.
"""

import io
import json
import re
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Literal, Protocol

import numpy as np
from PIL import Image
from pydantic import BaseModel, Field

from backend.core.settings import Settings
from backend.domain.adstudio.evaluator.readback import TextRead
from backend.domain.adstudio.image_clients import FAKE_NATIVE_SIZES, fake_product_box
from backend.domain.adstudio.sizing import SIZE_CLASSES
from backend.guardrails.context import wrap_untrusted
from backend.llm.gateway import LlmGateway
from backend.llm.prompts.ad_inspect import AD_INSPECT, CHECKLIST_IDS
from backend.llm.prompts.composition_judge import COMPOSITION_JUDGE
from backend.llm.prompts.context_judge import CONTEXT_JUDGE
from backend.llm.prompts.product_profile import PRODUCT_PROFILE

YesNo = Literal["yes", "no", "unsure"]
_HEX = re.compile(r"^#[0-9A-Fa-f]{6}$")
_UNSAFE_SURFACE = re.compile(r"[«»‹›<>\"`{}\[\]\r\n]+")


class ProductProfile(BaseModel):
    is_product: bool
    category: str = ""
    short_name: str = ""
    dominant_colors: list[str] = Field(default_factory=list[str])
    visible_text: list[str] = Field(default_factory=list[str])
    distinctive_features: list[str] = Field(default_factory=list[str])
    box_2d: list[int] = Field(
        default_factory=list[int], description="[ymin, xmin, ymax, xmax] normalised 0-1000"
    )
    has_label: bool = False
    size_class: str | None = Field(
        default=None, description="tiny | small | medium | large | xlarge (real-world size)"
    )
    approx_max_dimension_cm: float | None = Field(
        default=None, description="Approximate real-world largest dimension in centimetres"
    )
    typical_surface: str = Field(
        default="", description="Where the product usually rests (e.g. 'a table top')"
    )

    def sanitized(self) -> "ProductProfile":
        """Clamp to the documented shape (the model's JSON is untrusted structure)."""
        return ProductProfile(
            is_product=self.is_product,
            category=self.category.strip()[:80],
            short_name=self.short_name.strip()[:40],
            dominant_colors=[c.upper() for c in self.dominant_colors if _HEX.match(c)][:4],
            visible_text=[t.strip()[:120] for t in self.visible_text if t.strip()][:12],
            distinctive_features=[f.strip()[:100] for f in self.distinctive_features][:5],
            box_2d=list(valid_box(self.box_2d) or []),
            has_label=self.has_label,
            size_class=self.size_class if self.size_class in SIZE_CLASSES else None,
            approx_max_dimension_cm=(
                round(float(self.approx_max_dimension_cm), 1)
                if self.approx_max_dimension_cm is not None
                and 0.5 <= self.approx_max_dimension_cm <= 1000
                else None
            ),
            typical_surface=" ".join(_UNSAFE_SURFACE.sub(" ", self.typical_surface).split())[:60],
        )


class CheckVerdict(BaseModel):
    id: str
    evidence: str = Field(default="", description="What is visible; written before the verdict")
    verdict: YesNo


class DetectedProduct(BaseModel):
    box_2d: list[int] = Field(description="[ymin, xmin, ymax, xmax] normalised 0-1000")
    matches_reference: YesNo = "unsure"


class AdInspection(BaseModel):
    products: list[DetectedProduct] = Field(default_factory=list[DetectedProduct])
    product_count: int = 0
    checklist: list[CheckVerdict] = Field(default_factory=list[CheckVerdict])
    visible_text: list[TextRead] = Field(default_factory=list[TextRead])


class ContextVerdict(BaseModel):
    checks: list[CheckVerdict] = Field(default_factory=list[CheckVerdict])


class ScaleEstimate(BaseModel):
    """`composition_judge` v2: the product's real size implied by the scene object whose real
    size is most certain. Written before the verdicts (evidence first); code computes the
    implied/expected ratio against the product's known size."""

    reference_object: str = Field(
        default="", description="The scene object whose real-world size is most certain"
    )
    reference_size_cm: float | None = Field(
        default=None, description="That object's real size in cm (the dimension compared)"
    )
    product_implied_cm: float | None = Field(
        default=None,
        description="The product's largest dimension in cm implied by that object",
    )


class CompositionVerdict(BaseModel):
    """`composition_judge` answers (ADR-007): realistic scale and natural integration. v2 adds
    the scale estimate and up to 3 signs the product was pasted in, both before the checks."""

    scale: ScaleEstimate | None = None
    pasted_signs: list[str] = Field(
        default_factory=list[str],
        description="Up to 3 concrete visible signs that the product was pasted in; [] if none",
    )
    checks: list[CheckVerdict] = Field(default_factory=list[CheckVerdict])


class ContextCheck(BaseModel):
    """One binary context question (compiled by code from the spec's rubric and avoid list)."""

    id: str
    question: str
    severity: Literal["must", "should"]
    pass_on: Literal["yes", "no"]


def valid_box(box: list[int] | tuple[int, ...] | None) -> tuple[int, int, int, int] | None:
    """A usable [ymin, xmin, ymax, xmax] 0..1000 box, clamped; None when degenerate."""
    if box is None or len(box) != 4:
        return None
    ymin, xmin, ymax, xmax = (max(0, min(1000, int(v))) for v in box)
    if ymax - ymin < 5 or xmax - xmin < 5:
        return None
    return ymin, xmin, ymax, xmax


class VisionClient(Protocol):
    name: str

    def configured(self) -> bool: ...

    @property
    def model(self) -> str: ...

    async def profile(self, reference: bytes) -> ProductProfile: ...

    async def inspect(self, reference: bytes, ad: bytes, *, summary: str) -> AdInspection: ...

    async def judge_context(self, ad: bytes, checks: list[ContextCheck]) -> ContextVerdict: ...

    async def judge_composition(
        self, ad: bytes, checks: list[ContextCheck]
    ) -> CompositionVerdict: ...


class VisionUnavailableError(RuntimeError):
    """No vision judge is configured (the evaluator marks its checks `unverified`)."""


# --- gateway-backed (Gemini) ----------------------------------------------------------------------


def rubric_json(checks: list[ContextCheck]) -> str:
    return json.dumps(
        [{"id": c.id, "question": c.question} for c in checks], ensure_ascii=False, indent=1
    )


class GatewayVisionClient:
    name = "gateway"

    def __init__(self, gateway: LlmGateway, *, cache_version: str) -> None:
        self.gateway = gateway
        # The judge-answer cache key part (evaluator_config `judge_cache_version`).
        self.cache_version = cache_version

    def configured(self) -> bool:
        return self.gateway.vision_configured()

    @property
    def model(self) -> str:
        spec = self.gateway.vision_spec()
        return spec.raw if spec else ""

    async def profile(self, reference: bytes) -> ProductProfile:
        result = await self.gateway.run_vision(
            PRODUCT_PROFILE,
            images=[reference],
            labels=["Reference photo:"],
            output_type=ProductProfile,
        )
        return result.output.sanitized()

    async def inspect(self, reference: bytes, ad: bytes, *, summary: str) -> AdInspection:
        result = await self.gateway.run_vision(
            AD_INSPECT,
            images=[reference, ad],
            labels=["Image 1 (REFERENCE product photo):", "Image 2 (generated AD):"],
            text=wrap_untrusted("product_summary", "data", summary) if summary else "",
            output_type=AdInspection,
            key_parts=[self.cache_version],
        )
        return result.output

    async def judge_context(self, ad: bytes, checks: list[ContextCheck]) -> ContextVerdict:
        result = await self.gateway.run_vision(
            CONTEXT_JUDGE,
            images=[ad],
            labels=["The generated AD:"],
            text="CHECKS:\n" + wrap_untrusted("rubric", "data", rubric_json(checks)),
            output_type=ContextVerdict,
            key_parts=[self.cache_version],
        )
        return result.output

    async def judge_composition(self, ad: bytes, checks: list[ContextCheck]) -> CompositionVerdict:
        result = await self.gateway.run_vision(
            COMPOSITION_JUDGE,
            images=[ad],
            labels=["The generated AD:"],
            text="CHECKS:\n" + wrap_untrusted("checks", "data", rubric_json(checks)),
            output_type=CompositionVerdict,
            key_parts=[self.cache_version],
        )
        return result.output


class NoVisionClient:
    name = "none"
    model = ""

    def configured(self) -> bool:
        return False

    async def profile(self, reference: bytes) -> ProductProfile:
        raise VisionUnavailableError("no vision judge configured")

    async def inspect(self, reference: bytes, ad: bytes, *, summary: str) -> AdInspection:
        raise VisionUnavailableError("no vision judge configured")

    async def judge_context(self, ad: bytes, checks: list[ContextCheck]) -> ContextVerdict:
        raise VisionUnavailableError("no vision judge configured")

    async def judge_composition(self, ad: bytes, checks: list[ContextCheck]) -> CompositionVerdict:
        raise VisionUnavailableError("no vision judge configured")


# --- fake -----------------------------------------------------------------------------------------

FAKE_VISION_MODEL = "test:fake-vision"


def locate_fake_product(reference: bytes, ad: bytes) -> tuple[int, int, int, int]:
    """Where `render_fake_ad` pasted the reference: the scale (0.12-0.67) that matches best.

    Returns a 0-1000 [ymin, xmin, ymax, xmax] box. Only meaningful for fake-client images.
    """
    with Image.open(io.BytesIO(reference)) as ref_img:
        ref_size = ref_img.size
        ref_small = np.asarray(ref_img.convert("RGB").resize((24, 24)), np.float32)
    with Image.open(io.BytesIO(ad)) as ad_img:
        img = ad_img.convert("RGB")
    aw, ah = img.size
    aspect = "4:5" if abs(aw / ah - 0.8) < abs(aw / ah - 1.0) else "1:1"
    nw, nh = FAKE_NATIVE_SIZES[aspect]
    best: tuple[float, tuple[int, int, int, int]] = (float("inf"), (500, 250, 950, 750))
    for step in range(56):
        x0, y0, x1, y1 = fake_product_box(ref_size, (nw, nh), 0.12 + step * 0.01)
        box = (y0 * 1000 // nh, x0 * 1000 // nw, y1 * 1000 // nh, x1 * 1000 // nw)
        px = (x0 * aw // nw, y0 * ah // nh, x1 * aw // nw, y1 * ah // nh)
        if px[2] - px[0] < 2 or px[3] - px[1] < 2:
            continue
        crop = np.asarray(img.crop(px).resize((24, 24)), np.float32)
        diff = float(np.abs(crop - ref_small).mean())
        if diff < best[0]:
            best = (diff, box)
    return best[1]


@dataclass
class FakeVisionClient:
    """Scripted vision answers. Every field can be overridden per test; `fail` simulates an outage.

    - `box`: the product box to report; by default `locate_fake_product` finds the fake's paste.
    - `checklist` / `context` / `composition`: verdict overrides by id (defaults: favourable).
    - `reads`: the blind text read-back; by default the lines the test says are rendered (none).
    """

    name: str = "fake"
    box: tuple[int, int, int, int] | None = None
    product_count: int = 1
    checklist: dict[str, YesNo] = field(default_factory=dict[str, YesNo])
    context: dict[str, YesNo] = field(default_factory=dict[str, YesNo])
    composition: dict[str, YesNo] = field(default_factory=dict[str, YesNo])
    scale: ScaleEstimate | None = None
    pasted_signs: list[str] = field(default_factory=list[str])
    reads: list[TextRead] | None = None
    profile_answer: ProductProfile | None = None
    fail: BaseException | None = None
    locate: Callable[[bytes, bytes], tuple[int, int, int, int]] | None = locate_fake_product
    calls: list[str] = field(default_factory=list[str])

    def configured(self) -> bool:
        return True

    @property
    def model(self) -> str:
        return FAKE_VISION_MODEL

    def _maybe_fail(self, what: str) -> None:
        self.calls.append(what)
        if self.fail is not None:
            raise self.fail

    async def profile(self, reference: bytes) -> ProductProfile:
        self._maybe_fail("profile")
        if self.profile_answer is not None:
            return self.profile_answer
        return ProductProfile(
            is_product=True,
            category="product",
            short_name="Product",
            box_2d=[40, 40, 960, 960],
        )

    async def inspect(self, reference: bytes, ad: bytes, *, summary: str) -> AdInspection:
        self._maybe_fail("inspect")
        box = self.box or (self.locate(reference, ad) if self.locate else (500, 250, 950, 750))
        products = [DetectedProduct(box_2d=list(box), matches_reference="yes")]
        products += [
            DetectedProduct(box_2d=[0, 0, 200, 200], matches_reference="yes")
            for _ in range(max(0, self.product_count - 1))
        ]
        return AdInspection(
            products=products[: max(0, self.product_count)],
            product_count=self.product_count,
            checklist=[
                CheckVerdict(id=i, evidence="(fake)", verdict=self.checklist.get(i, "yes"))
                for i in CHECKLIST_IDS
            ],
            visible_text=list(self.reads or []),
        )

    async def judge_context(self, ad: bytes, checks: list[ContextCheck]) -> ContextVerdict:
        self._maybe_fail("context")
        return ContextVerdict(
            checks=[
                CheckVerdict(id=c.id, evidence="(fake)", verdict=self.context.get(c.id, c.pass_on))
                for c in checks
            ]
        )

    async def judge_composition(self, ad: bytes, checks: list[ContextCheck]) -> CompositionVerdict:
        self._maybe_fail("composition")
        return CompositionVerdict(
            scale=self.scale,
            pasted_signs=list(self.pasted_signs),
            checks=[
                CheckVerdict(
                    id=c.id, evidence="(fake)", verdict=self.composition.get(c.id, c.pass_on)
                )
                for c in checks
            ],
        )


def build_vision_client(
    settings: Settings, gateway: LlmGateway, *, cache_version: str
) -> VisionClient:
    if settings.vision_client == "fake":
        return FakeVisionClient()
    return GatewayVisionClient(gateway, cache_version=cache_version)
