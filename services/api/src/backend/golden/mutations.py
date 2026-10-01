"""Planted-failure mutations (ai-design §9.1): pure Pillow / numpy / scikit-image, no OpenCV.

Every mutation is `fn(source_png_bytes, params, products) -> bytes` and is fully determined by its
params (seeded choices are made when the params are planned, never here), so
`planted/manifest.jsonl` regenerates the exact image from the source output. Regions use the
evaluator's conventions: text zones are normalised `[x0, y0, x1, y1]`, product boxes are 0-1000
`[ymin, xmin, ymax, xmax]`.

Inpainting is `skimage.restoration.inpaint_biharmonic` on a downscaled crop (the stand-in for
`cv2.inpaint`, see docs/stack-lock.md). Product regions use the evaluator's background mask: pixels
far (dE76) from the median colour of the crop's border ring.
"""

import io
from collections.abc import Callable
from typing import Any, cast

import numpy as np
from numpy.typing import NDArray
from PIL import Image, ImageDraw, ImageEnhance, ImageFilter, ImageFont
from skimage import measure as _skmeasure  # pyright: ignore[reportMissingTypeStubs]
from skimage import morphology as _skmorphology  # pyright: ignore[reportMissingTypeStubs]
from skimage import restoration as _skrestoration  # pyright: ignore[reportMissingTypeStubs]

from backend.domain.adstudio.evaluator.color import box_to_pixels, rgb2lab
from backend.domain.adstudio.imaging import encode_png
from backend.domain.adstudio.layout import NormBox
from backend.domain.adstudio.overlay import Overlay, OverlayZone, font_path, needs_shaping

ProductLoader = Callable[[str], bytes]
Mutation = Callable[[bytes, dict[str, Any], ProductLoader], bytes]
BoolArray = NDArray[np.bool_]
FloatArray = NDArray[np.float64]
_sk = cast(Any, _skrestoration)
_skm = cast(Any, _skmeasure)
_skmo = cast(Any, _skmorphology)
STRAY_FONT = "NotoSans-Bold.ttf"

# Which dimension each class breaks (ai-design §9.1). Generated classes live in plant.py.
FAILS: dict[str, list[str]] = {
    "text_typo": ["text"],
    "text_missing": ["text"],
    "text_stray": ["text"],
    "product_recolour": ["product"],
    "product_swap": ["product"],
    "product_duplicate": ["product"],
    "product_logo_erased": ["product"],
    "technical": ["technical"],
    "control_good": [],
    "context_season": ["context"],
    "context_geo": ["context"],
    "comp_oversize": ["composition"],
    "comp_pasted": ["composition"],
}


def open_rgb(data: bytes) -> Image.Image:
    with Image.open(io.BytesIO(data)) as img:
        return img.convert("RGB")


def _hex(color: str) -> tuple[int, int, int]:
    c = color.lstrip("#")
    return int(c[0:2], 16), int(c[2:4], 16), int(c[4:6], 16)


def _rgb_hex(rgb: NDArray[Any]) -> str:
    r, g, b = (int(round(float(v))) for v in rgb[:3])
    return f"#{r:02X}{g:02X}{b:02X}"


def zone_pixels(
    zone: list[float], w: int, h: int, margin: float = 0.0
) -> tuple[int, int, int, int]:
    box = NormBox(x0=zone[0], y0=zone[1], x1=zone[2], y1=zone[3])
    if margin:
        box = box.expanded(margin)
    return box.to_pixels(w, h)


def _box(params: dict[str, Any], w: int, h: int) -> tuple[int, int, int, int]:
    ymin, xmin, ymax, xmax = (int(v) for v in params["box"])
    return box_to_pixels((ymin, xmin, ymax, xmax), w, h)


def _lab(img: Image.Image) -> FloatArray:
    return rgb2lab(np.asarray(img.convert("RGB"), dtype=np.float64) / 255.0)


def border_median(lab: FloatArray, fraction: float) -> FloatArray:
    h, w, _ = lab.shape
    ring = max(1, round(min(h, w) * fraction))
    border = np.concatenate(
        [
            lab[:ring].reshape(-1, 3),
            lab[-ring:].reshape(-1, 3),
            lab[:, :ring].reshape(-1, 3),
            lab[:, -ring:].reshape(-1, 3),
        ]
    )
    return np.median(border, axis=0)


def foreground_mask(
    img: Image.Image, fraction: float = 0.06, delta: float = 12.0, min_fraction: float = 0.2
) -> BoolArray:
    """The evaluator's product mask: pixels far from the crop's border colour. Whole crop when the
    mask is under `min_fraction` of it (a product filling its box)."""
    lab = _lab(img)
    mask = np.linalg.norm(lab - border_median(lab, fraction), axis=2) > delta
    if float(mask.mean()) < min_fraction:
        return np.ones(mask.shape, dtype=np.bool_)
    return mask


def dilate(mask: BoolArray, radius: int) -> BoolArray:
    """Square dilation by shifting (no scipy needed)."""
    if radius <= 0:
        return mask
    h, w = mask.shape
    padded = np.pad(mask, radius)
    out = np.zeros_like(mask)
    for dy in range(2 * radius + 1):
        for dx in range(2 * radius + 1):
            out |= padded[dy : dy + h, dx : dx + w]
    return out


def inpaint(img: Image.Image, mask: BoolArray, scale: int = 2) -> Image.Image:
    """Biharmonic inpainting of `mask` on a `scale`-times downscaled copy; only masked pixels of
    the original are replaced."""
    if not mask.any():
        return img.copy()
    w, h = img.size
    sw, sh = max(8, w // scale), max(8, h // scale)
    small = np.asarray(img.resize((sw, sh), Image.Resampling.BILINEAR), dtype=np.float64) / 255.0
    mask_img = Image.fromarray(mask.astype(np.uint8) * 255).resize((sw, sh), Image.Resampling.BOX)
    small_mask = np.asarray(mask_img) > 0
    filled = np.asarray(
        _sk.inpaint_biharmonic(small, small_mask, split_into_regions=True, channel_axis=-1)
    )
    filled_img = Image.fromarray((np.clip(filled, 0, 1) * 255).round().astype(np.uint8))
    filled_full = np.asarray(filled_img.resize((w, h), Image.Resampling.BILINEAR))
    out = np.asarray(img, dtype=np.uint8).copy()
    out[mask] = filled_full[mask]
    return Image.fromarray(out)


# --- text -----------------------------------------------------------------------------------------


def text_typo(source: bytes, params: dict[str, Any], _: ProductLoader) -> bytes:
    """Inpaint the zone and re-render the headline with one edit (the overlay's Noto font)."""
    typo = str(params["typo"])
    zone = OverlayZone(
        box=NormBox(**dict(zip(("x0", "y0", "x1", "y1"), params["zone"], strict=True))),
        band_color=str(params["band_color"]),
        max_lines=int(params.get("max_lines", 3)),
    )
    # Latin copy renders with Pillow's basic layout so the image is the same on every machine.
    overlay = Overlay(raqm=None if needs_shaping(typo) else False)
    return overlay.render(source, zone, typo).data


def text_missing(source: bytes, params: dict[str, Any], _: ProductLoader) -> bytes:
    """Inpaint the headline glyphs (pixels far from the zone's band colour) away."""
    img = open_rgb(source)
    w, h = img.size
    x0, y0, x1, y1 = zone_pixels(list(params["zone"]), w, h, float(params.get("margin", 0.0)))
    crop = img.crop((x0, y0, x1, y1))
    lab = _lab(crop)
    band = np.median(lab.reshape(-1, 3), axis=0)
    mask = np.linalg.norm(lab - band, axis=2) > float(params.get("delta", 30.0))
    mask = dilate(mask, int(params.get("dilate", 3)))
    filled = inpaint(crop, mask, int(params.get("scale", 2)))
    out = img.copy()
    out.paste(filled, (x0, y0))
    return encode_png(out)


def text_stray(source: bytes, params: dict[str, Any], _: ProductLoader) -> bytes:
    """A gibberish word outside the zone ("SALEE XQ")."""
    img = open_rgb(source)
    w, h = img.size
    size = max(12, round(float(params["size"]) * h))
    font = ImageFont.truetype(
        str(font_path(STRAY_FONT)), size, layout_engine=ImageFont.Layout.BASIC
    )
    ImageDraw.Draw(img).text(
        (round(float(params["x"]) * w), round(float(params["y"]) * h)),
        str(params["text"]),
        font=font,
        fill=_hex(str(params["fill"])),
        stroke_width=round(float(params.get("stroke_width", 0.0)) * size),
        stroke_fill=_hex(str(params.get("stroke", "#000000"))),
    )
    return encode_png(img)


# --- product --------------------------------------------------------------------------------------


def product_recolour(source: bytes, params: dict[str, Any], _: ProductLoader) -> bytes:
    """Hue rotation inside the product mask of the box."""
    img = open_rgb(source)
    left, top, right, bottom = _box(params, *img.size)
    crop = img.crop((left, top, right, bottom))
    mask = foreground_mask(
        crop, float(params.get("border_fraction", 0.06)), float(params.get("delta", 12.0))
    )
    shift = round(float(params["hue_deg"]) / 360 * 256) % 256
    hh, ss, vv = crop.convert("HSV").split()
    rotated = Image.merge("HSV", (hh.point([(t + shift) % 256 for t in range(256)]), ss, vv))
    rot = np.asarray(rotated.convert("RGB"))
    out_crop = np.asarray(crop).copy()
    out_crop[mask] = rot[mask]
    out = img.copy()
    out.paste(Image.fromarray(out_crop), (left, top))
    return encode_png(out)


def _fill_box(img: Image.Image, box: tuple[int, int, int, int]) -> None:
    """Replace the box with a vertical gradient between its top and bottom edge colours."""
    left, top, right, bottom = box
    arr = np.asarray(img, dtype=np.float64)
    band = max(1, (bottom - top) // 50)
    top_c = np.median(arr[max(0, top - band) : top + band, left:right].reshape(-1, 3), axis=0)
    bot_c = np.median(arr[bottom - band : bottom + band, left:right].reshape(-1, 3), axis=0)
    height = bottom - top
    t = np.linspace(0.0, 1.0, max(1, height))[:, None, None]
    grad = top_c[None, None, :] * (1 - t) + bot_c[None, None, :] * t
    grad = np.broadcast_to(grad, (height, right - left, 3))
    img.paste(Image.fromarray(grad.round().astype(np.uint8)), (left, top))


def _cutout(
    photo: Image.Image, fraction: float, delta: float, feather: float
) -> tuple[Image.Image, Image.Image]:
    """(RGB crop, L alpha) of the product in a photo, trimmed to its mask's bounding box."""
    # A cut-out keeps even a thin product (a bottle on a white sweep is < 20% of its photo).
    mask = foreground_mask(photo, fraction, delta, min_fraction=0.02)
    ys, xs = np.nonzero(mask)
    crop_box = (int(xs.min()), int(ys.min()), int(xs.max()) + 1, int(ys.max()) + 1)
    alpha = Image.fromarray(mask.astype(np.uint8) * 255).crop(crop_box)
    if feather > 0:
        alpha = alpha.filter(ImageFilter.GaussianBlur(feather))
    return photo.crop(crop_box), alpha


def _fit(size: tuple[int, int], box: tuple[int, int], fill: float) -> tuple[int, int]:
    w, h = size
    s = min(box[0] * fill / w, box[1] * fill / h)
    return max(1, round(w * s)), max(1, round(h * s))


def product_swap(source: bytes, params: dict[str, Any], products: ProductLoader) -> bytes:
    """Erase the product and paste another golden product (alpha-matted) at the same box."""
    img = open_rgb(source)
    box = _box(params, *img.size)
    left, top, right, bottom = box
    fraction, delta = float(params.get("border_fraction", 0.06)), float(params.get("delta", 12.0))
    _fill_box(img, box)
    partner = open_rgb(products(str(params["partner"])))
    partner.thumbnail((768, 768), Image.Resampling.LANCZOS)
    cut, alpha = _cutout(partner, fraction, delta, float(params.get("feather", 1.5)))
    size = _fit(cut.size, (right - left, bottom - top), float(params.get("fill", 0.95)))
    cut = cut.resize(size, Image.Resampling.LANCZOS)
    alpha = alpha.resize(size, Image.Resampling.LANCZOS)
    x = left + (right - left - size[0]) // 2
    y = bottom - size[1]
    img.paste(cut, (x, y), alpha)
    return encode_png(img)


def product_duplicate(source: bytes, params: dict[str, Any], _: ProductLoader) -> bytes:
    """A second, smaller copy of the product beside it."""
    img = open_rgb(source)
    w, h = img.size
    left, top, right, bottom = _box(params, w, h)
    crop = img.crop((left, top, right, bottom))
    cut, alpha = _cutout(
        crop,
        float(params.get("border_fraction", 0.06)),
        float(params.get("delta", 12.0)),
        float(params.get("feather", 1.5)),
    )
    margin = round(0.03 * w)
    # Fit the copy into the free space beside the product when it can (0.3x at the smallest).
    free = (left if params["anchor"] == "left" else w - right) - 2 * margin
    scale = max(0.3, min(float(params["scale"]), free / max(1, cut.width)))
    size = (max(1, round(cut.width * scale)), max(1, round(cut.height * scale)))
    cut = cut.resize(size, Image.Resampling.LANCZOS)
    alpha = alpha.resize(size, Image.Resampling.LANCZOS)
    x = margin if params["anchor"] == "left" else w - margin - size[0]
    y = bottom - size[1]
    img.paste(cut, (x, y), alpha)
    return encode_png(img)


def logo_mask(crop: Image.Image, params: dict[str, Any]) -> BoolArray:
    """Logo / label pixels: product pixels far from the product's body (median) colour."""
    lab = _lab(crop)
    fg = foreground_mask(
        crop, float(params.get("border_fraction", 0.06)), float(params.get("bg_delta", 12.0))
    )
    body = np.median(lab[fg], axis=0)
    mask = fg & (np.linalg.norm(lab - body, axis=2) > float(params.get("delta", 25.0)))
    return dilate(mask, int(params.get("dilate", 2)))


def product_logo_erased(source: bytes, params: dict[str, Any], _: ProductLoader) -> bytes:
    """Inpaint the logo / label region of the product with the surrounding body colour."""
    img = open_rgb(source)
    left, top, right, bottom = _box(params, *img.size)
    crop = img.crop((left, top, right, bottom))
    mask = logo_mask(crop, params)
    filled = inpaint(crop, mask, int(params.get("scale", 2)))
    out = img.copy()
    out.paste(filled, (left, top))
    return encode_png(out)


# --- composition (ADR-007) ------------------------------------------------------------------------


def _tight(mask: BoolArray) -> BoolArray:
    """The largest connected region of the mask with its holes filled (the product, without the
    background objects that also differ from the box's border colour)."""
    labels = np.asarray(_skm.label(mask, connectivity=2))
    if int(labels.max()) == 0:
        return mask
    counts = np.bincount(labels.ravel())
    counts[0] = 0
    keep = labels == int(counts.argmax())
    return np.asarray(_skmo.remove_small_holes(keep, max_size=int(keep.size)), dtype=np.bool_)


def _product_cutout(
    img: Image.Image, box: tuple[int, int, int, int], params: dict[str, Any]
) -> tuple[Image.Image, BoolArray]:
    """The product's pixels inside its box (the evaluator's background mask, tightened) and the
    mask."""
    crop = img.crop(box)
    mask = foreground_mask(
        crop,
        float(params.get("border_fraction", 0.06)),
        float(params.get("delta", 12.0)),
        min_fraction=0.02,
    )
    return crop, _tight(mask)


def _erase(
    img: Image.Image, box: tuple[int, int, int, int], mask: BoolArray, *, below: int = 0
) -> Image.Image:
    """Inpaint the product (its mask, grown 3 px) and a band of `below` px under its base (the
    contact shadow) from the surroundings."""
    w, h = img.size
    left, top, right, bottom = box
    full = np.zeros((h, w), dtype=np.bool_)
    full[top:bottom, left:right] = dilate(mask, 3)
    if below > 0:
        cols = np.nonzero(mask.any(axis=0))[0]
        if cols.size:
            x0, x1 = left + int(cols.min()), left + int(cols.max()) + 1
            y0 = max(0, bottom - max(2, below // 2))
            full[y0 : min(h, bottom + below), max(0, x0 - 4) : min(w, x1 + 4)] = True
    return inpaint(img, full, 3)


def comp_oversize(source: bytes, params: dict[str, Any], _: ProductLoader) -> bytes:
    """The product scaled up in place (about 1.8x, anchored at its base): too big for the scene.
    The scale is capped at plan time so the product stays clear of the headline zone."""
    img = open_rgb(source)
    left, top, right, bottom = _box(params, *img.size)
    crop, mask = _product_cutout(img, (left, top, right, bottom), params)
    scale = float(params["scale"])
    alpha = Image.fromarray(mask.astype(np.uint8) * 255).filter(
        ImageFilter.GaussianBlur(float(params.get("feather", 1.0)))
    )
    size = (max(1, round(crop.width * scale)), max(1, round(crop.height * scale)))
    big = crop.resize(size, Image.Resampling.LANCZOS)
    big_alpha = alpha.resize(size, Image.Resampling.LANCZOS)
    img = _erase(img, (left, top, right, bottom), mask)
    x = (left + right) // 2 - size[0] // 2
    y = bottom - size[1]
    img.paste(big, (x, y), big_alpha)
    return encode_png(img)


def comp_pasted(source: bytes, params: dict[str, Any], _: ProductLoader) -> bytes:
    """The product cut out and pasted back like a sticker: its surroundings (and contact shadow)
    replaced by a plain fill, lifted off the surface, with a hard edge and a light halo."""
    img = open_rgb(source)
    w, h = img.size
    left, top, right, bottom = _box(params, w, h)
    crop, mask = _product_cutout(img, (left, top, right, bottom), params)
    # Erase the product and its contact shadow (a band of `shadow` of the height under its base).
    img = _erase(
        img, (left, top, right, bottom), mask, below=round(float(params.get("shadow", 0.04)) * h)
    )
    lift = round(float(params.get("lift", 0.03)) * h)
    x, y = left, max(0, top - lift)
    halo_px = int(params.get("halo_px", 4))
    halo = Image.fromarray(dilate(mask, halo_px).astype(np.uint8) * 255).filter(
        ImageFilter.GaussianBlur(1.0)
    )
    glow = Image.new("RGB", crop.size, _hex(str(params.get("halo_color", "#F4F4F0"))))
    strength = float(params.get("halo_alpha", 0.85))
    halo_alpha = Image.fromarray(
        (np.asarray(halo, dtype=np.float64) * strength).round().astype(np.uint8)
    )
    img.paste(glow, (x, y), halo_alpha)
    img.paste(crop, (x, y), Image.fromarray(mask.astype(np.uint8) * 255))  # hard edge
    return encode_png(img)


# --- technical and controls -----------------------------------------------------------------------


def technical(source: bytes, params: dict[str, Any], _: ProductLoader) -> bytes:
    img = open_rgb(source)
    w, h = img.size
    variant = params["variant"]
    if variant == "upscale_2048":
        s = 2048 / max(w, h)
        return encode_png(img.resize((round(w * s), round(h * s)), Image.Resampling.LANCZOS))
    if variant == "wrong_aspect":
        side = min(w, h)
        if w == h:  # a square ad becomes 16:9
            crop = (0, (h - round(w * 9 / 16)) // 2, w, (h + round(w * 9 / 16)) // 2)
        else:
            crop = ((w - side) // 2, (h - side) // 2, (w + side) // 2, (h + side) // 2)
        return encode_png(img.crop(crop))
    if variant == "blank":
        mean = np.asarray(img, dtype=np.float64).reshape(-1, 3).mean(axis=0)
        return encode_png(Image.new("RGB", (w, h), _hex(_rgb_hex(mean))))
    if variant == "truncated_jpeg":
        buf = io.BytesIO()
        img.save(buf, format="JPEG", quality=90)
        data = buf.getvalue()
        return data[: round(len(data) * float(params.get("keep", 0.6)))]
    raise ValueError(f"unknown technical variant {variant!r}")


def control_good(source: bytes, params: dict[str, Any], _: ProductLoader) -> bytes:
    """Known-good controls: unmodified, JPEG q=80 re-encode, brightness +8%."""
    variant = str(params["variant"])
    if variant == "identity":
        return source
    img = open_rgb(source)
    if "brightness" in variant:
        img = ImageEnhance.Brightness(img).enhance(float(params.get("factor", 1.08)))
    if "jpeg" in variant:
        buf = io.BytesIO()
        img.save(buf, format="JPEG", quality=int(params.get("quality", 80)))
        return buf.getvalue()
    return encode_png(img)


MUTATIONS: dict[str, Mutation] = {
    "text_typo": text_typo,
    "text_missing": text_missing,
    "text_stray": text_stray,
    "product_recolour": product_recolour,
    "product_swap": product_swap,
    "product_duplicate": product_duplicate,
    "product_logo_erased": product_logo_erased,
    "technical": technical,
    "control_good": control_good,
    "comp_oversize": comp_oversize,
    "comp_pasted": comp_pasted,
}


def apply_mutation(
    mutation: str, source: bytes, params: dict[str, Any], products: ProductLoader
) -> bytes:
    try:
        fn = MUTATIONS[mutation]
    except KeyError as exc:
        raise ValueError(f"{mutation!r} is not a pure mutation") from exc
    return fn(source, params, products)
