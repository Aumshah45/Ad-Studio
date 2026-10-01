"""Upload validation and normalisation (production-readiness "Upload validation spec").

Order matters and every failure is problem+json:
1. size cap (413) · 2. format allowlist from magic bytes, never the client content type (415) ·
3. pixel cap and minimum edge from the header, before any decode (413 / 422), decompression-bomb
guard · 4. single frame (422) · 5. decode in a worker thread with a timeout, truncated files
rejected (422) · 6. EXIF orientation applied, ICC -> sRGB, RGB/RGBA, long edge <= 2048 ·
7. re-encode to PNG with no EXIF, ICC or text chunks. The original bytes are discarded.
"""

import asyncio
import io
import warnings
from dataclasses import dataclass

import structlog
from PIL import Image, ImageCms, ImageFile, ImageOps, UnidentifiedImageError

from backend.core.errors import AppError

MAX_UPLOAD_BYTES = 10_485_760
MAX_PIXELS = 40_000_000
MIN_EDGE = 256
MAX_EDGE = 2048
DECODE_TIMEOUT_S = 5.0
ALLOWED_FORMATS = ("PNG", "JPEG", "WEBP")

# Pillow's own bomb guard at our cap (it only raises at 2x by default; the warning is escalated
# below). Truncated images must fail, never be padded.
Image.MAX_IMAGE_PIXELS = MAX_PIXELS
ImageFile.LOAD_TRUNCATED_IMAGES = False
_SRGB = ImageCms.ImageCmsProfile(ImageCms.createProfile("sRGB"))  # pyright: ignore[reportUnknownMemberType, reportUnknownArgumentType]
log = structlog.get_logger(__name__)


@dataclass(frozen=True)
class NormalizedImage:
    data: bytes  # PNG, metadata-free
    width: int
    height: int
    source_format: str


def _too_large(detail: str) -> AppError:
    return AppError(413, "payload-too-large", "Image too large", detail)


def _invalid(detail: str, reason: str) -> AppError:
    return AppError(422, "validation-error", "Invalid image", detail, reason=reason)


def _unsupported() -> AppError:
    return AppError(
        415,
        "unsupported-media-type",
        "Unsupported image type",
        "Upload a PNG, JPEG or WebP image.",
    )


def _open_header(data: bytes) -> Image.Image:
    """Parse the header only (lazy open); formats come from magic bytes."""
    with warnings.catch_warnings():
        warnings.simplefilter("error", Image.DecompressionBombWarning)
        try:
            img = Image.open(io.BytesIO(data), formats=[*ALLOWED_FORMATS])
        except (Image.DecompressionBombError, Image.DecompressionBombWarning) as exc:
            raise _too_large(f"Image exceeds {MAX_PIXELS:,} pixels.") from exc
        except UnidentifiedImageError as exc:
            raise _unsupported() from exc
        except (OSError, SyntaxError, ValueError) as exc:
            raise _invalid("The image header could not be read.", "corrupt") from exc
    # JPEGs with extra frames (MPO) report their own format; the primary frame is a plain JPEG.
    if img.format not in (*ALLOWED_FORMATS, "MPO"):
        raise _unsupported()
    return img


def _to_srgb(img: Image.Image) -> Image.Image:
    has_alpha = img.mode in ("RGBA", "LA", "PA") or (img.mode == "P" and "transparency" in img.info)
    target = "RGBA" if has_alpha else "RGB"
    icc = img.info.get("icc_profile")
    if icc:
        try:
            src = ImageCms.ImageCmsProfile(io.BytesIO(icc))
            base = img if img.mode in ("RGB", "RGBA", "CMYK", "L") else img.convert(target)
            converted = ImageCms.profileToProfile(base, src, _SRGB, outputMode=target)
            if converted is not None:
                return converted
        except (ImageCms.PyCMSError, OSError, ValueError) as exc:
            log.info("upload_icc_ignored", error=type(exc).__name__)
    return img.convert(target)


def _decode_and_normalize(img: Image.Image) -> Image.Image:
    try:
        img.load()
    except (OSError, SyntaxError, ValueError) as exc:
        raise _invalid("The image is truncated or corrupt.", "corrupt") from exc
    try:
        img = _to_srgb(ImageOps.exif_transpose(img))
    except (OSError, ValueError) as exc:
        raise _invalid("The image pixel format is not supported.", "pixel-format") from exc
    if max(img.size) > MAX_EDGE:
        img.thumbnail((MAX_EDGE, MAX_EDGE), Image.Resampling.LANCZOS)
    return img


def _encode_png(img: Image.Image) -> bytes:
    img.info = {}  # PNG save falls back to info["icc_profile"]; drop every metadata chunk
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


async def normalize_upload(
    data: bytes,
    *,
    max_bytes: int = MAX_UPLOAD_BYTES,
    decode_timeout_s: float = DECODE_TIMEOUT_S,
) -> NormalizedImage:
    if len(data) > max_bytes:
        raise _too_large(f"Upload exceeds {max_bytes:,} bytes.")
    if not data:
        raise _invalid("The upload is empty.", "empty")
    img = _open_header(data)
    width, height = img.size
    source_format = "JPEG" if img.format == "MPO" else str(img.format)
    if width * height > MAX_PIXELS:
        raise _too_large(f"Image is {width}x{height}; the limit is {MAX_PIXELS:,} pixels.")
    if min(width, height) < MIN_EDGE:
        raise _invalid(
            f"Image is {width}x{height}; the short edge must be at least {MIN_EDGE} px.",
            "too-small",
        )
    if source_format != "JPEG" and getattr(img, "n_frames", 1) != 1:
        raise _invalid("Animated images are not supported.", "animated")

    def work() -> tuple[bytes, int, int]:
        out = _decode_and_normalize(img)
        return _encode_png(out), out.width, out.height

    try:
        png, w, h = await asyncio.wait_for(asyncio.to_thread(work), timeout=decode_timeout_s)
    except TimeoutError as exc:
        raise _invalid("The image took too long to decode.", "decode-timeout") from exc
    finally:
        img.close()
    return NormalizedImage(data=png, width=w, height=h, source_format=source_format)
