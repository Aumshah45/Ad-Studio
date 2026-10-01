"""Technical checks (ai-design §5.1): deterministic, cheapest first, short-circuit the rest.

| check           | signal                                   | pass rule            |
| decodes         | Pillow open + load                       | no error             |
| resolution      | max(w, h)                                | <= 1024              |
| aspect          | w/h vs requested                         | within 1.5%          |
| not_blank       | luminance std-dev                        | > 8.0                |
| not_copy        | SSIM(ad, reference resized to the ad)    | < 0.92               |
| not_placeholder | fraction of near-uniform 32 px tiles     | < 0.85               |
"""

import io
import warnings

import numpy as np
from numpy.typing import NDArray
from PIL import Image, UnidentifiedImageError

from backend.domain.adstudio.evaluator.config import TechnicalConfig
from backend.domain.adstudio.evaluator.schemas import CheckResult, DimensionResult
from backend.domain.adstudio.evaluator.schemas import dimension_from_checks as _dimension
from backend.domain.adstudio.imaging import MAX_EDGE, resize

ASPECTS: dict[str, float] = {"1:1": 1.0, "4:5": 4 / 5}
SSIM_WIDTH = 256
FloatArray = NDArray[np.float64]


def _check(
    name: str, passed: bool, value: float | None, threshold: float | None, evidence: str
) -> CheckResult:
    return CheckResult(
        dimension="technical",
        name=name,
        passed=passed,
        value=value,
        threshold=threshold,
        evidence="" if passed else evidence,
    )


def decode_for_checks(data: bytes) -> Image.Image | None:
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            img = Image.open(io.BytesIO(data), formats=["PNG", "JPEG", "WEBP"])
            img.load()
        return img.convert("RGB")
    except (
        UnidentifiedImageError,
        Image.DecompressionBombError,
        Image.DecompressionBombWarning,
        OSError,
        SyntaxError,
        ValueError,
    ):
        return None


def gray(img: Image.Image) -> FloatArray:
    return np.asarray(img.convert("L"), dtype=np.float64)


def _box_mean(x: FloatArray, k: int) -> FloatArray:
    """Mean over every k x k window ('valid' region) via 2-D cumulative sums."""
    c = np.cumsum(np.cumsum(np.pad(x, ((1, 0), (1, 0))), axis=0), axis=1)
    s = c[k:, k:] - c[:-k, k:] - c[k:, :-k] + c[:-k, :-k]
    return s / (k * k)


def ssim(a: FloatArray, b: FloatArray, win: int = 7) -> float:
    """Mean SSIM with a uniform window (Wang et al. 2004 constants), images in 0..255."""
    c1, c2 = (0.01 * 255) ** 2, (0.03 * 255) ** 2
    mu_a, mu_b = _box_mean(a, win), _box_mean(b, win)
    var_a = _box_mean(a * a, win) - mu_a**2
    var_b = _box_mean(b * b, win) - mu_b**2
    cov = _box_mean(a * b, win) - mu_a * mu_b
    num = (2 * mu_a * mu_b + c1) * (2 * cov + c2)
    den = (mu_a**2 + mu_b**2 + c1) * (var_a + var_b + c2)
    return float(np.mean(num / den))


def similarity_to_reference(img: Image.Image, reference: Image.Image) -> float:
    h = max(8, round(img.height * SSIM_WIDTH / img.width))
    a = gray(resize(img, (SSIM_WIDTH, h)))
    b = gray(resize(reference.convert("RGB"), (SSIM_WIDTH, h)))
    return ssim(a, b)


def image_similarity(a: bytes, b: bytes) -> float | None:
    """SSIM between two encoded images at the check resolution (None if either can't decode).
    Used to stop a repair loop whose edit left the image (almost) unchanged."""
    img_a, img_b = decode_for_checks(a), decode_for_checks(b)
    if img_a is None or img_b is None:
        return None
    return similarity_to_reference(img_a, img_b)


def uniform_tile_fraction(img: Image.Image, tile: int, std_max: float) -> float:
    g = gray(img)
    rows, cols = g.shape[0] // tile, g.shape[1] // tile
    if rows == 0 or cols == 0:
        return 1.0
    tiles = g[: rows * tile, : cols * tile].reshape(rows, tile, cols, tile).swapaxes(1, 2)
    stds = tiles.reshape(rows, cols, tile * tile).std(axis=2)
    return float(np.mean(stds < std_max))


def technical_checks(
    img: Image.Image | None,
    aspect_ratio: str,
    cfg: TechnicalConfig,
    reference: Image.Image | None = None,
) -> list[CheckResult]:
    if img is None:
        return [_check("decodes", False, None, None, "The image could not be decoded.")]
    checks = [_check("decodes", True, None, None, "")]
    w, h = img.size
    long_edge = max(w, h)
    checks.append(
        _check(
            "resolution",
            long_edge <= MAX_EDGE,
            float(long_edge),
            float(MAX_EDGE),
            f"Long edge is {long_edge} px; the cap is {MAX_EDGE} px.",
        )
    )
    want = ASPECTS.get(aspect_ratio)
    if want is not None:
        got = w / h
        err = abs(got - want) / want
        checks.append(
            _check(
                "aspect",
                err <= cfg.aspect_tolerance,
                round(err, 5),
                cfg.aspect_tolerance,
                f"Aspect is {w}x{h} ({got:.3f}); requested {aspect_ratio} ({want:.3f}).",
            )
        )
    std = float(gray(img).std())
    checks.append(
        _check(
            "not_blank",
            std > cfg.blank_std_min,
            round(std, 3),
            cfg.blank_std_min,
            f"The image is nearly blank (luminance std-dev {std:.1f}).",
        )
    )
    if reference is not None:
        sim = similarity_to_reference(img, reference)
        checks.append(
            _check(
                "not_copy",
                sim < cfg.copy_ssim_max,
                round(sim, 4),
                cfg.copy_ssim_max,
                f"The image is a near copy of the reference photo (SSIM {sim:.2f}).",
            )
        )
    frac = uniform_tile_fraction(img, cfg.placeholder_tile_px, cfg.placeholder_tile_std)
    checks.append(
        _check(
            "not_placeholder",
            frac < cfg.placeholder_fraction_max,
            round(frac, 4),
            cfg.placeholder_fraction_max,
            f"{frac:.0%} of the image is flat tiles (placeholder or unrendered).",
        )
    )
    return checks


def technical_dimension(checks: list[CheckResult]) -> DimensionResult:
    return _dimension("technical", checks)
