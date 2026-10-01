import io

import pytest
from PIL import Image

from backend.domain.adstudio.imaging import (
    MAX_EDGE,
    InvalidGeneratedImageError,
    capped_size,
    postprocess_generated,
)
from backend.storage.blobs import BlobStore


def _jpeg(width: int, height: int) -> bytes:
    img = Image.new("RGB", (width, height), (30, 120, 200))
    img.paste((250, 40, 40), (0, 0, width // 2, height // 2))
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=90)
    return buf.getvalue()


@pytest.mark.parametrize(
    "size", [(928, 1152), (1152, 928), (2048, 2048), (1024, 1024), (1400, 700), (600, 400)]
)
def test_resolution_cap(size: tuple[int, int], tmp_path) -> None:  # type: ignore[no-untyped-def]
    out = postprocess_generated(_jpeg(*size))
    with Image.open(io.BytesIO(out.data)) as img:
        assert img.format == "PNG"
        assert max(img.size) <= MAX_EDGE
        assert img.size == (out.width, out.height)
    assert (out.native_width, out.native_height) == size
    assert out.downscaled == (max(size) > MAX_EDGE)
    # The generated write path accepts every post-processed image.
    stored = BlobStore(tmp_path).put_generated(out.data)
    assert max(stored.width, stored.height) <= MAX_EDGE


@pytest.mark.parametrize(
    ("size", "expected"),
    [
        ((928, 1152), (825, 1024)),  # 4:5 "1K" from the model
        ((1152, 928), (1024, 825)),
        ((2048, 2048), (1024, 1024)),
        ((3000, 1000), (1024, 341)),
        ((800, 1000), (800, 1000)),  # already under the cap: untouched
    ],
)
def test_downscale_preserves_aspect(size: tuple[int, int], expected: tuple[int, int]) -> None:
    assert capped_size(*size) == expected
    out = postprocess_generated(_jpeg(*size))
    assert (out.width, out.height) == expected
    # Aspect error is at most half a pixel on the short edge.
    w, h = size
    assert abs(out.width / out.height - w / h) <= 0.5 / min(expected) * (w / h) + 1e-9
    assert max(out.width, out.height) == min(MAX_EDGE, max(size))


def test_postprocess_rejects_garbage() -> None:
    with pytest.raises(InvalidGeneratedImageError):
        postprocess_generated(b"<svg/>")
