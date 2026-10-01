"""Image-generation clients behind one protocol (architecture "Generator", ai-design §3).

- `GeminiImageClient`: google-genai 2.25.0. The Interactions API is primary and `generate_content`
  is the alternative adapter. **Unverified until the slice-1 live spike** records which surface and
  model IDs work (docs/stack-lock.md); both are kept behind this protocol so the switch is a
  setting (`IMAGE_API`).
- `FakeImageClient`: no key, no network. It composites the reference product photo into a simple
  template scene (palette gradient, product lower-centre, headline band). Used by tests and by
  key-less dev (`IMAGE_CLIENT=fake`). A text edit (`repair_text`, candidate image only) repaints
  the headline band of the candidate. `FAKE_IMAGE_SCRIPT` scripts failures per request label
  (a typo'd headline, a recoloured product, stray text, a blank image, a safety block) so the whole
  repair -> overlay flow can be demonstrated offline.

Clients do not retry, time out, cache or record: the generator wraps every call in `guarded_call`.
A refusal or an answer without an image raises `SafetyBlockedError`, which is never retried.
"""

import base64
import hashlib
import io
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field, replace
from typing import Any, Literal, Protocol, cast

import numpy as np
from PIL import Image, ImageDraw

from backend.core.settings import Settings
from backend.domain.adstudio.fonts import Font, load_font
from backend.domain.adstudio.imaging import resize
from backend.llm.calls import SafetyBlockedError
from backend.llm.ledger import current_run_id

AspectRatio = Literal["1:1", "4:5"]
ImageApi = Literal["interactions", "generate_content"]
FAKE_MODEL = "test:fake-image"


@dataclass(frozen=True)
class FakeScene:
    """Layout hints only the fake client uses; real models get everything from the prompt."""

    palette: tuple[str, ...] = ("#F4D35E", "#0D3B66")
    headline_lines: tuple[str, ...] = ()
    zone: tuple[float, float, float, float] = (0.06, 0.04, 0.94, 0.22)  # x0, y0, x1, y1
    band_color: str = "#0D3B66"
    text_color: str = "#FFFFFF"
    product_scale: float = 0.5


@dataclass(frozen=True)
class ImageRequest:
    prompt: str
    images: tuple[bytes, ...]  # reference first; the candidate second for edits
    aspect_ratio: AspectRatio
    image_size: str = "1K"
    scene: FakeScene | None = None
    # What the call is for (candidate, regenerate, clean_plate, repair_product, repair_context,
    # repair_text) and its slot. Real clients ignore both; the fake uses them to script failures.
    label: str = "candidate"
    slot: int = 0


@dataclass(frozen=True)
class ImageResult:
    data: bytes
    mime: str
    served_model: str
    meta: dict[str, Any] = field(default_factory=dict[str, Any])


class ImageClient(Protocol):
    name: str

    def configured(self) -> bool: ...

    def model_for(self, requested: str) -> str:
        """The model id the call is recorded under (the fake records `test:fake-image`)."""
        ...

    async def generate(self, request: ImageRequest, *, model: str) -> ImageResult: ...


def _model_id(spec: str) -> str:
    provider, _, name = spec.partition(":")
    if provider != "google" or not name:
        raise ValueError(f"not a google image model spec: {spec!r}")
    return name


def _b64(data: bytes) -> str:
    return base64.b64encode(data).decode("ascii")


def _sniff_mime(data: bytes) -> str:
    if data.startswith(b"\x89PNG"):
        return "image/png"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp"
    return "image/jpeg"


class GeminiImageClient:
    """google-genai image generation. UNVERIFIED until the slice-1 spike (see module docstring)."""

    name = "gemini"

    def __init__(self, api_key: str, api: ImageApi = "interactions") -> None:
        self._api_key = api_key
        self.api: ImageApi = api
        self._client: Any = None

    def configured(self) -> bool:
        return bool(self._api_key)

    def model_for(self, requested: str) -> str:
        return requested

    def _genai(self) -> Any:
        if self._client is None:
            from google import genai

            self._client = genai.Client(api_key=self._api_key)
        return self._client

    async def generate(self, request: ImageRequest, *, model: str) -> ImageResult:
        if self.api == "interactions":
            return await self._interactions(request, model)
        return await self._generate_content(request, model)

    async def _interactions(self, request: ImageRequest, model: str) -> ImageResult:
        parts: list[dict[str, Any]] = [
            {"type": "image", "data": _b64(img), "mime_type": _sniff_mime(img)}
            for img in request.images
        ]
        parts.append({"type": "text", "text": request.prompt})
        interaction: Any = await self._genai().aio.interactions.create(
            model=_model_id(model),
            input=parts,
            response_format={
                "type": "image",
                "aspect_ratio": request.aspect_ratio,
                "image_size": request.image_size,
                "mime_type": "image/jpeg",
            },
        )
        status = str(getattr(interaction, "status", ""))
        image: Any = getattr(interaction, "output_image", None)
        data = getattr(image, "data", None) if image is not None else None
        if not data:
            text = str(getattr(interaction, "output_text", "") or "")[:200]
            raise SafetyBlockedError(f"no image returned (status={status}) {text}".strip())
        raw = base64.b64decode(cast(str, data))
        return ImageResult(
            data=raw,
            mime=str(getattr(image, "mime_type", None) or _sniff_mime(raw)),
            served_model=model,
            meta={"api": "interactions", "status": status},
        )

    async def _generate_content(self, request: ImageRequest, model: str) -> ImageResult:
        from google.genai import types

        contents: list[Any] = [
            types.Part.from_bytes(data=img, mime_type=_sniff_mime(img)) for img in request.images
        ]
        contents.append(request.prompt)
        config = types.GenerateContentConfig(
            response_modalities=["IMAGE"],
            image_config=types.ImageConfig(
                aspect_ratio=request.aspect_ratio, image_size=request.image_size
            ),
        )
        response: Any = await self._genai().aio.models.generate_content(
            model=_model_id(model), contents=contents, config=config
        )
        candidates: list[Any] = list(getattr(response, "candidates", None) or [])
        finish = str(getattr(candidates[0], "finish_reason", "")) if candidates else "none"
        for cand in candidates:
            content: Any = getattr(cand, "content", None)
            for part in list(getattr(content, "parts", None) or []):
                inline: Any = getattr(part, "inline_data", None)
                if inline is not None and getattr(inline, "data", None):
                    raw = cast(bytes, inline.data)
                    return ImageResult(
                        data=raw,
                        mime=str(getattr(inline, "mime_type", None) or _sniff_mime(raw)),
                        served_model=model,
                        meta={"api": "generate_content", "finish_reason": finish},
                    )
        raise SafetyBlockedError(f"no image returned (finish_reason={finish})")


# --- fake -----------------------------------------------------------------------------------------

# Native sizes the fake returns: 4:5 "1K" comes back above 1024 px on the long edge, like the real
# model is expected to, so the downscale path is always exercised.
FAKE_NATIVE_SIZES: dict[str, tuple[int, int]] = {"1:1": (1024, 1024), "4:5": (928, 1152)}


def _hex(color: str) -> tuple[int, int, int]:
    c = color.lstrip("#")
    return int(c[0:2], 16), int(c[2:4], 16), int(c[4:6], 16)


def _gradient(size: tuple[int, int], top: str, bottom: str) -> Image.Image:
    w, h = size
    a, b = _hex(top), _hex(bottom)
    column = Image.new("RGB", (1, h))
    for y in range(h):
        t = y / max(1, h - 1)
        column.putpixel((0, y), tuple(round(a[i] + (b[i] - a[i]) * t) for i in range(3)))
    return resize(column, (w, h), Image.Resampling.NEAREST)


def _fit_font(draw: ImageDraw.ImageDraw, lines: tuple[str, ...], box_w: int, box_h: int) -> Font:
    size = max(12, box_h // max(1, len(lines)))
    while size > 12:
        font = load_font(size)
        widths = [draw.textbbox((0, 0), line, font=font)[2] for line in lines]
        if max(widths, default=0) <= box_w and size * 1.25 * len(lines) <= box_h:
            return font
        size -= 2
    return load_font(12)


def fake_product_box(
    reference_size: tuple[int, int], canvas_size: tuple[int, int], scale: float
) -> tuple[int, int, int, int]:
    """Pixel box (x0, y0, x1, y1) where `render_fake_ad` pastes the product: lower centre."""
    rw, rh = reference_size
    w, h = canvas_size
    target_h = max(1, round(h * scale))
    target_w = max(1, round(rw * target_h / rh))
    if target_w > round(w * 0.8):
        target_w = round(w * 0.8)
        target_h = max(1, round(rh * target_w / rw))
    x0 = (w - target_w) // 2
    y0 = h - target_h - round(h * 0.06)
    return x0, y0, x0 + target_w, y0 + target_h


def draw_headline(canvas: Image.Image, scene: FakeScene) -> None:
    """The headline band in the zone, with `scene.headline_lines` centred in it (in place)."""
    w, h = canvas.size
    zx0, zy0, zx1, zy1 = scene.zone
    box = (round(zx0 * w), round(zy0 * h), round(zx1 * w), round(zy1 * h))
    draw = ImageDraw.Draw(canvas)
    draw.rectangle(box, fill=_hex(scene.band_color))
    lines = scene.headline_lines
    if lines:
        pad = round((box[3] - box[1]) * 0.12)
        inner_w, inner_h = box[2] - box[0] - 2 * pad, box[3] - box[1] - 2 * pad
        font = _fit_font(draw, lines, inner_w, inner_h)
        line_h = round(getattr(font, "size", 12) * 1.25)
        y = box[1] + ((box[3] - box[1]) - line_h * len(lines)) // 2
        for line in lines:
            left, _, right, _ = draw.textbbox((0, 0), line, font=font)
            x = box[0] + ((box[2] - box[0]) - (right - left)) // 2 - left
            draw.text((x, y), line, font=font, fill=_hex(scene.text_color))
            y += line_h


def render_fake_ad(
    reference: bytes, aspect_ratio: str, scene: FakeScene, *, seed: str = ""
) -> Image.Image:
    """Palette gradient + the product photo lower-centre + a headline band in the zone."""
    size = FAKE_NATIVE_SIZES.get(aspect_ratio, (1024, 1024))
    palette = scene.palette or ("#DDDDDD", "#888888")
    shift = int(hashlib.sha256(seed.encode()).hexdigest()[:2], 16) % max(1, len(palette))
    top, bottom = palette[shift % len(palette)], palette[(shift + 1) % len(palette)]
    canvas = _gradient(size, top, bottom)
    # Mild photographic grain so the fake is not a flat placeholder (technical check). Seeded:
    # PIL's effect_noise draws from the C library's global rand() state, so the same fake ad
    # came out different depending on what ran earlier in the process (flaky ΔE near a limit).
    rng = np.random.default_rng(int(hashlib.sha256(f"grain:{seed}".encode()).hexdigest()[:8], 16))
    noise = np.clip(rng.normal(128.0, 40.0, (size[1], size[0])), 0, 255).astype(np.uint8)
    grain = Image.fromarray(noise, mode="L").convert("RGB")
    canvas = Image.blend(canvas, grain, 0.12)

    with Image.open(io.BytesIO(reference)) as ref_img:
        product = ref_img.convert("RGB")
    x0, y0, x1, y1 = fake_product_box(product.size, size, scene.product_scale)
    product = resize(product, (x1 - x0, y1 - y0))
    canvas.paste(product, (x0, y0))
    draw_headline(canvas, scene)
    return canvas


# --- scripted failures (offline demo and tests) --------------------------------------------

# "echo": an edit that returns the image it was given unchanged (a stalled repair).
FakeBehaviour = Literal["ok", "typo", "recolor", "stray", "blank", "block", "echo"]
FAKE_BEHAVIOURS: tuple[FakeBehaviour, ...] = (
    "ok",
    "typo",
    "recolor",
    "stray",
    "blank",
    "block",
    "echo",
)
FAKE_LABELS = (
    "candidate",
    "regenerate",
    "clean_plate",
    "repair_product",
    "repair_context",
    "repair_text",
)
STRAY_TEXT = "FREE GIFT"
_VOWELS = set("aeiouAEIOU")


def parse_fake_script(text: str) -> dict[str, tuple[FakeBehaviour, ...]]:
    """`"candidate=typo;repair_text=typo,ok"` -> {label: behaviours}. Raises ValueError if bad."""
    script: dict[str, tuple[FakeBehaviour, ...]] = {}
    for part in text.split(";"):
        if not part.strip():
            continue
        label, sep, values = part.partition("=")
        label = label.strip()
        if not sep or label.split(".")[0] not in FAKE_LABELS:
            raise ValueError(f"FAKE_IMAGE_SCRIPT: unknown label in {part.strip()!r}")
        behaviours: list[FakeBehaviour] = []
        for value in values.split(","):
            value = value.strip()
            if value not in FAKE_BEHAVIOURS:
                raise ValueError(f"FAKE_IMAGE_SCRIPT: unknown behaviour {value!r}")
            behaviours.append(value)
        script[label] = tuple(behaviours)
    return script


def typo_line(line: str) -> str:
    """A plausible model typo: drop the last vowel of the first word of 4+ letters ("Summr")."""
    words = line.split(" ")
    for i, word in enumerate(words):
        if sum(ch.isalpha() for ch in word) >= 4:
            for j in range(len(word) - 1, 0, -1):
                if word[j] in _VOWELS:
                    words[i] = word[:j] + word[j + 1 :]
                    return " ".join(words)
    stripped = line.rstrip()
    return stripped[:-1] if len(stripped) > 1 else line + "x"


def _recolour(img: Image.Image, box: tuple[int, int, int, int]) -> None:
    """Rotate the hue of the product region by about 120 degrees (in place)."""
    region = img.crop(box).convert("HSV")
    h, s, v = region.split()
    h = h.point([(x + 85) % 256 for x in range(256)])
    img.paste(Image.merge("HSV", (h, s, v)).convert("RGB"), box[:2])


def _stray(img: Image.Image, scene: FakeScene) -> None:
    """Promotional text the brief never asked for, below the zone and left of the product."""
    w, h = img.size
    y = round(min(0.9, scene.zone[3] + 0.08) * h)
    ImageDraw.Draw(img).text(
        (round(0.05 * w), y),
        STRAY_TEXT,
        font=load_font(max(28, h // 18)),
        fill=(255, 255, 255),
        stroke_width=3,
        stroke_fill=(0, 0, 0),
    )


class FakeImageClient:
    """Deterministic local composite; counts calls. `transform` lets tests alter the output.

    `script` (or `FAKE_IMAGE_SCRIPT`) makes chosen calls fail in a scripted way; behaviours are
    counted per run (the ledger's run id) and per label, so every run replays the same story.
    """

    name = "fake"

    def __init__(
        self,
        *,
        transform: Callable[[Image.Image, ImageRequest], Image.Image] | None = None,
        block: bool = False,
        script: str | dict[str, tuple[FakeBehaviour, ...]] | None = None,
    ) -> None:
        self.transform = transform
        self.block = block
        if isinstance(script, str):
            self.script = parse_fake_script(script)
        else:
            self.script = dict(script or {})
        self.calls: list[ImageRequest] = []
        self.behaviours: list[tuple[str, FakeBehaviour]] = []
        self._counts: dict[tuple[uuid.UUID | None, str], int] = {}

    @property
    def cache_tag(self) -> str:
        """Part of the generator's cache key, so scripted and clean images never mix."""
        if not self.script:
            return ""
        return ";".join(f"{k}={','.join(v)}" for k, v in sorted(self.script.items()))

    def configured(self) -> bool:
        return True

    def model_for(self, requested: str) -> str:
        return FAKE_MODEL

    def behaviour_for(self, request: ImageRequest) -> FakeBehaviour:
        slotted = f"{request.label}.{request.slot}"
        key = slotted if slotted in self.script else request.label
        plan = self.script.get(key)
        if not plan:
            return "ok"
        counter = (current_run_id.get(), key)
        n = self._counts.get(counter, 0)
        self._counts[counter] = n + 1
        return plan[min(n, len(plan) - 1)]

    def _render(self, request: ImageRequest, behaviour: FakeBehaviour) -> Image.Image:
        scene = request.scene or FakeScene()
        if behaviour == "typo" and scene.headline_lines:
            typoed = tuple(typo_line(ln) for ln in scene.headline_lines)
            scene = replace(scene, headline_lines=typoed)
        size = FAKE_NATIVE_SIZES.get(request.aspect_ratio, (1024, 1024))
        if behaviour == "blank":
            return Image.new("RGB", size, (128, 128, 128))
        if behaviour == "echo":
            with Image.open(io.BytesIO(request.images[-1])) as last:
                return last.convert("RGB")
        if request.label == "repair_text" and len(request.images) == 1:
            # An edit of the candidate: repaint the headline band, keep everything else.
            with Image.open(io.BytesIO(request.images[0])) as cand:
                img = cand.convert("RGB")
            draw_headline(img, scene)
            return img
        seed = hashlib.sha256(request.prompt.encode()).hexdigest()
        img = render_fake_ad(request.images[0], request.aspect_ratio, scene, seed=seed)
        if behaviour == "recolor":
            with Image.open(io.BytesIO(request.images[0])) as ref:
                ref_size = ref.size
            _recolour(img, fake_product_box(ref_size, size, scene.product_scale))
        elif behaviour == "stray":
            _stray(img, scene)
        return img

    async def generate(self, request: ImageRequest, *, model: str) -> ImageResult:
        self.calls.append(request)
        behaviour = self.behaviour_for(request)
        self.behaviours.append((request.label, behaviour))
        if self.block or behaviour == "block":
            raise SafetyBlockedError("fake safety block")
        if not request.images:
            raise ValueError("the fake client needs the reference image")
        img = self._render(request, behaviour)
        if self.transform is not None:
            img = self.transform(img, request)
        buf = io.BytesIO()
        img.save(buf, format="JPEG", quality=92)
        return ImageResult(
            data=buf.getvalue(),
            mime="image/jpeg",
            served_model=model,
            meta={"api": "fake", "behaviour": behaviour},
        )


def build_image_client(settings: Settings) -> ImageClient:
    if settings.image_client == "fake":
        return FakeImageClient(script=settings.fake_image_script)
    return GeminiImageClient(settings.google_api_key.get_secret_value(), api=settings.image_api)
