"""Product colour fidelity (ai-design §5.3 step 3): CIEDE2000 between dominant colours.

Crop the product box in the reference and in the ad -> separate the product from its background
(the median colour of the crop's border ring) -> seeded k-means (k=3) in CIELAB -> match each
reference colour to the nearest ad colour -> `delta_e = sum(w_i * dE2000(ref_i, ad_i))` with
kL = 2 (lightness counts half, so scene lighting is tolerated while hue and chroma drift count in
full). The weighted mean dilutes a recoloured part (a red logo band on a white mug), so the check
also fails when any colour with weight >= `cluster_min_weight` drifts past `cluster_delta_e_max`.
Deterministic: fixed seeds, fixed sample size.
"""

from dataclasses import dataclass, field
from typing import Any, cast

import numpy as np
from numpy.typing import NDArray
from PIL import Image
from skimage import color as _skcolor  # pyright: ignore[reportMissingTypeStubs]

from backend.domain.adstudio.evaluator.config import ProductConfig

Box = tuple[int, int, int, int]  # [ymin, xmin, ymax, xmax] normalised 0-1000
FloatArray = NDArray[np.float64]
_sk = cast(Any, _skcolor)  # scikit-image ships no type information


def rgb2lab(rgb: FloatArray) -> FloatArray:
    return np.asarray(_sk.rgb2lab(rgb), dtype=np.float64)


def lab2rgb(lab: FloatArray) -> FloatArray:
    return np.asarray(_sk.lab2rgb(lab), dtype=np.float64)


def box_to_pixels(box: Box, width: int, height: int) -> tuple[int, int, int, int]:
    """0-1000 [ymin, xmin, ymax, xmax] -> Pillow (left, top, right, bottom), at least 1 px."""
    ymin, xmin, ymax, xmax = box
    x0, y0 = round(xmin * width / 1000), round(ymin * height / 1000)
    x1, y1 = round(xmax * width / 1000), round(ymax * height / 1000)
    return x0, y0, max(x0 + 1, x1), max(y0 + 1, y1)


def _lab(img: Image.Image, box: Box | None, max_px: int) -> FloatArray:
    crop = img.convert("RGB")
    if box is not None:
        crop = crop.crop(box_to_pixels(box, crop.width, crop.height))
    scale = min(1.0, max_px / max(crop.size))
    if scale < 1.0:
        size = (max(1, round(crop.width * scale)), max(1, round(crop.height * scale)))
        crop = crop.resize(size, Image.Resampling.BILINEAR)
    rgb = np.asarray(crop, dtype=np.float64) / 255.0
    return rgb2lab(rgb)


def foreground(lab: FloatArray, cfg: ProductConfig) -> tuple[FloatArray, float]:
    """Product pixels of a crop: those far (dE76) from the median border colour.

    Falls back to the whole crop when the mask is under `mask_min_fraction` of it (a product that
    fills its box, or a busy background). Returns (N x 3 Lab pixels, mask fraction).
    """
    h, w, _ = lab.shape
    ring = max(1, round(min(h, w) * cfg.border_fraction))
    border = np.concatenate(
        [
            lab[:ring].reshape(-1, 3),
            lab[-ring:].reshape(-1, 3),
            lab[:, :ring].reshape(-1, 3),
            lab[:, -ring:].reshape(-1, 3),
        ]
    )
    background = np.median(border, axis=0)
    distance = np.linalg.norm(lab - background, axis=2)
    mask = distance > cfg.background_delta
    fraction = float(mask.mean())
    if fraction < cfg.mask_min_fraction:
        return lab.reshape(-1, 3), 1.0
    return lab[mask], fraction


def kmeans(points: FloatArray, k: int, seed: int, sample: int) -> tuple[FloatArray, FloatArray]:
    """Seeded k-means++ / Lloyd. Returns (centres k x 3, weights summing to 1), heaviest first."""
    rng = np.random.default_rng(seed)
    if len(points) > sample:
        points = points[rng.choice(len(points), size=sample, replace=False)]
    k = max(1, min(k, len(points)))
    centres: list[FloatArray] = [points[rng.integers(len(points))]]
    for _ in range(1, k):
        d2: FloatArray = np.stack([np.sum((points - c) ** 2, axis=1) for c in centres]).min(axis=0)
        total = float(d2.sum())
        if total <= 0:
            break
        centres.append(points[rng.choice(len(points), p=d2 / total)])
    c = np.array(centres, dtype=np.float64)
    labels = np.zeros(len(points), dtype=np.int64)
    for _ in range(25):
        dist = np.sum((points[:, None, :] - c[None, :, :]) ** 2, axis=2)
        labels = np.argmin(dist, axis=1)
        moved = np.array(
            [
                points[labels == i].mean(axis=0) if np.any(labels == i) else c[i]
                for i in range(len(c))
            ]
        )
        if np.allclose(moved, c, atol=1e-3):
            c = moved
            break
        c = moved
    weights = np.bincount(labels, minlength=len(c)).astype(np.float64)
    weights /= weights.sum()
    order = np.argsort(-weights)
    return c[order], weights[order]


def ciede2000(a: FloatArray, b: FloatArray, k_l: float) -> FloatArray:
    return np.asarray(_sk.deltaE_ciede2000(a, b, kL=k_l), dtype=np.float64)


def chroma(lab: FloatArray) -> float:
    """CIELAB chroma C*ab."""
    return float(np.hypot(lab[1], lab[2]))


def hue_difference(a: FloatArray, b: FloatArray) -> float:
    """Absolute CIELAB hue-angle difference h_ab in degrees (0-180)."""
    ha = np.degrees(np.arctan2(a[2], a[1]))
    hb = np.degrees(np.arctan2(b[2], b[1]))
    d = abs(float(ha - hb)) % 360
    return min(d, 360 - d)


def lab_to_hex(lab: FloatArray) -> str:
    rgb = np.clip(lab2rgb(lab.reshape(1, 1, 3)), 0.0, 1.0)
    r, g, b = (round(float(v) * 255) for v in rgb.reshape(3))
    return f"#{r:02X}{g:02X}{b:02X}"


@dataclass(frozen=True)
class ColourComparison:
    delta_e: float  # weighted over the reference's dominant colours
    worst: float  # the largest drift of any colour with weight >= cluster_min_weight
    reference_colors: list[str]
    ad_colors: list[str]
    weights: list[float]
    per_colour: list[float]
    reference_mask: float
    ad_mask: float
    # ev-0.8: CIELAB hue-angle difference (degrees) per reference colour; None for a colour too
    # grey to have a hue (chroma below `hue_chroma_min`) or too small (weight below
    # `cluster_min_weight`). Measured only: it never fails the product (user ruling).
    hue_drift: list[float | None] = field(default_factory=list[float | None])

    @property
    def max_hue_drift(self) -> float | None:
        drifts = [d for d in self.hue_drift if d is not None]
        return max(drifts) if drifts else None


def dominant_colours(
    img: Image.Image, box: Box | None, cfg: ProductConfig
) -> tuple[FloatArray, FloatArray, float]:
    pixels, fraction = foreground(_lab(img, box, cfg.crop_max_px), cfg)
    centres, weights = kmeans(pixels, cfg.kmeans_k, cfg.kmeans_seed, cfg.sample_px)
    return centres, weights, fraction


def compare_colours(
    reference: Image.Image,
    reference_box: Box | None,
    ad: Image.Image,
    ad_box: Box,
    cfg: ProductConfig,
) -> ColourComparison:
    ref_c, ref_w, ref_frac = dominant_colours(reference, reference_box, cfg)
    ad_c, _, ad_frac = dominant_colours(ad, ad_box, cfg)
    per: list[float] = []
    matched: list[str] = []
    hues: list[float | None] = []
    for centre, weight in zip(ref_c, ref_w, strict=True):
        distances = ciede2000(np.repeat(centre[None, :], len(ad_c), axis=0), ad_c, cfg.delta_e_kl)
        j = int(np.argmin(distances))
        per.append(float(distances[j]))
        matched.append(lab_to_hex(ad_c[j]))
        chromatic = chroma(centre) >= cfg.hue_chroma_min and chroma(ad_c[j]) >= cfg.hue_chroma_min
        hues.append(
            round(hue_difference(centre, ad_c[j]), 1)
            if chromatic and weight >= cfg.cluster_min_weight
            else None
        )
    delta = float(np.sum(ref_w * np.array(per)))
    worst = max(
        (p for p, w in zip(per, ref_w, strict=True) if w >= cfg.cluster_min_weight), default=0.0
    )
    return ColourComparison(
        delta_e=round(delta, 2),
        worst=round(worst, 2),
        reference_colors=[lab_to_hex(c) for c in ref_c],
        ad_colors=matched,
        weights=[round(float(w), 3) for w in ref_w],
        per_colour=[round(p, 2) for p in per],
        reference_mask=round(ref_frac, 3),
        ad_mask=round(ad_frac, 3),
        hue_drift=hues,
    )
