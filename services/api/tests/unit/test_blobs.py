from pathlib import Path

import pytest

from backend.storage.blobs import (
    BlobNotFoundError,
    BlobStore,
    OversizeImageError,
    sha256_hex,
)
from tests.images import png


def test_blobstore_roundtrip(tmp_path: Path) -> None:
    store = BlobStore(tmp_path)
    data = png(300, 200)
    stored = store.put_image(data)
    assert stored.sha256 == sha256_hex(data)
    assert stored.path == tmp_path / stored.sha256[:2] / stored.sha256
    assert (stored.width, stored.height, stored.mime) == (300, 200, "image/png")
    assert store.read(stored.sha256) == data
    assert store.exists(stored.sha256)
    again = store.put(data)  # idempotent: same bytes, same path, no temp files left behind
    assert again.path == stored.path
    assert [p.name for p in stored.path.parent.iterdir()] == [stored.sha256]


def test_blobstore_rejects_bad_ids(tmp_path: Path) -> None:
    store = BlobStore(tmp_path)
    with pytest.raises(BlobNotFoundError):
        store.read("../../etc/passwd")
    with pytest.raises(BlobNotFoundError):
        store.read("0" * 64)
    assert not store.exists("not-a-sha")


def test_blob_generated_rejects_oversize(tmp_path: Path) -> None:
    store = BlobStore(tmp_path)
    ok = store.put_generated(png(1024, 820))
    assert (ok.width, ok.height) == (1024, 820)
    for w, h in ((1025, 800), (800, 1280), (2048, 2048)):
        with pytest.raises(OversizeImageError):
            store.put_generated(png(w, h))
    assert sorted(p.name for p in tmp_path.rglob("*") if p.is_file()) == [ok.sha256]
    with pytest.raises(ValueError, match="PNG, JPEG or WebP"):
        store.put_generated(b"<svg xmlns='http://www.w3.org/2000/svg'/>")
