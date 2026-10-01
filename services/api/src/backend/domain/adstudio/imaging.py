"""Post-processing of model-generated pixels: decode -> cap -> downscale -> re-encode.

C1: every stored generated image has a long edge <= 1024 px. A "1K" image at 4:5 comes back with a
long edge above 1024 (ai-design §3), so the downscale is mandatory, not defensive. The evaluator
always scores the downscaled artefact that ships.
"""

import io
import warnings
from dataclasses import dataclass

from PIL import Image, ImageOps, UnidentifiedImageError

MAX_EDGE = 1024
# Generated images are at most 4K (~16.8 MP); anything bigger is not a model output.
MAX_DECODE_PIXELS = 20_000_000
_FORMATS = ["PNG", "JPEG", "WEBP"]


class InvalidGeneratedImageError(ValueError):
    """The model returned bytes that are not a decodable PNG/JPEG/WebP image."""


@dataclass(frozen=True)
class ProcessedImage:
    data: bytes  # PNG, metadata-free
    width: int
    height: int
    native_width: int
    native_height: int
    mime: str = "image/png"

    @property
    def downscaled(self) -> bool:
        return (self.width, self.height) != (self.native_width, self.native_height)


def capped_size(width: int, height: int, cap: int = MAX_EDGE) -> tuple[int, int]:
    """The size after scaling so the long edge is <= `cap`, preserving the aspect ratio.

    The long edge lands exactly on `cap`; the short edge is rounded to the nearest pixel (>= 1).
    """
    if width <= 0 or height <= 0:
        raise ValueError(f"invalid size {width}x{height}")
    long_edge = max(width, height)
    if long_edge <= cap:
        return width, height
    scale = cap / long_edge
    if width >= height:
        return cap, max(1, round(height * scale))
    return max(1, round(width * scale)), cap


def resize(
    img: Image.Image,
    size: tuple[int, int],
    resample: Image.Resampling = Image.Resampling.LANCZOS,
) -> Image.Image:
    """Typed wrapper (Pillow's stub leaves `resize` partially unknown under strict pyright)."""
    return img.resize(size, resample)  # pyright: ignore[reportUnknownMemberType]


def downscale(img: Image.Image, cap: int = MAX_EDGE) -> Image.Image:
    size = capped_size(img.width, img.height, cap)
    if size == img.size:
        return img
    return resize(img, size)


def decode_image(data: bytes) -> Image.Image:
    """Fully decode allowlisted image bytes into RGB (alpha flattened on white)."""
    with warnings.catch_warnings():
        warnings.simplefilter("error", Image.DecompressionBombWarning)
        try:
            img = Image.open(io.BytesIO(data), formats=_FORMATS)
            if img.width * img.height > MAX_DECODE_PIXELS:
                raise InvalidGeneratedImageError(f"image is {img.width}x{img.height}; too large")
            img.load()
        except InvalidGeneratedImageError:
            raise
        except (
            UnidentifiedImageError,
            Image.DecompressionBombError,
            Image.DecompressionBombWarning,
            OSError,
            SyntaxError,
            ValueError,
        ) as exc:
            raise InvalidGeneratedImageError(f"undecodable image ({type(exc).__name__})") from exc
    img = ImageOps.exif_transpose(img)
    if img.mode in ("RGBA", "LA", "PA") or (img.mode == "P" and "transparency" in img.info):
        rgba = img.convert("RGBA")
        flat = Image.new("RGB", rgba.size, (255, 255, 255))
        flat.paste(rgba, mask=rgba.getchannel("A"))
        return flat
    return img.convert("RGB")


def encode_png(img: Image.Image) -> bytes:
    img.info = {}
    buf = io.BytesIO()
    img.save(buf, format="PNG", optimize=False)
    return buf.getvalue()


def postprocess_generated(data: bytes, cap: int = MAX_EDGE) -> ProcessedImage:
    """Decode, downscale to the cap and re-encode as a metadata-free PNG."""
    img = decode_image(data)
    native = img.size
    out = downscale(img, cap)
    if max(out.size) > cap:  # pragma: no cover - guarded by capped_size
        raise AssertionError("downscale did not honour the cap")
    return ProcessedImage(
        data=encode_png(out),
        width=out.width,
        height=out.height,
        native_width=native[0],
        native_height=native[1],
    )
