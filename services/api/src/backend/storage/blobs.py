"""Content-addressed blob store (ADR-003): `<root>/<sha[:2]>/<sha>`, sha256 of the stored bytes.

Writes are atomic (temp file + rename) and idempotent: the same bytes land at the same path once.
`put_generated` is the only write path for model-generated pixels and asserts the C1 cap (long
edge <= 1024 px) by decoding the image header, so an oversize image can never be stored.
"""

import hashlib
import io
import os
import re
import tempfile
from dataclasses import dataclass
from pathlib import Path

from PIL import Image, UnidentifiedImageError

MAX_GENERATED_EDGE = 1024
_SHA_RE = re.compile(r"^[0-9a-f]{64}$")


class BlobNotFoundError(LookupError):
    pass


class OversizeImageError(ValueError):
    """A generated image exceeds the 1024 px long-edge cap (C1)."""


@dataclass(frozen=True)
class StoredBlob:
    sha256: str
    size: int
    path: Path


@dataclass(frozen=True)
class StoredImage(StoredBlob):
    width: int
    height: int
    mime: str


_MIMES = {"PNG": "image/png", "JPEG": "image/jpeg", "WEBP": "image/webp"}


def sha256_hex(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def image_info(data: bytes) -> tuple[int, int, str]:
    """(width, height, mime) from the header only; raises ValueError for non-allowlisted formats."""
    try:
        with Image.open(io.BytesIO(data), formats=list(_MIMES)) as img:
            fmt = img.format or ""
            if fmt not in _MIMES:
                raise ValueError(f"unsupported image format {fmt!r}")
            return img.width, img.height, _MIMES[fmt]
    except UnidentifiedImageError as exc:
        raise ValueError("not a PNG, JPEG or WebP image") from exc


class BlobStore:
    def __init__(self, root: Path) -> None:
        self.root = root

    def path_for(self, sha256: str) -> Path:
        if not _SHA_RE.match(sha256):
            raise BlobNotFoundError("invalid blob id")
        return self.root / sha256[:2] / sha256

    def exists(self, sha256: str) -> bool:
        try:
            return self.path_for(sha256).is_file()
        except BlobNotFoundError:
            return False

    def put(self, data: bytes) -> StoredBlob:
        sha = sha256_hex(data)
        path = self.path_for(sha)
        if not path.is_file():
            path.parent.mkdir(parents=True, exist_ok=True)
            fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=".tmp-")
            try:
                with os.fdopen(fd, "wb") as fh:
                    fh.write(data)
                    fh.flush()
                    os.fsync(fh.fileno())
                os.replace(tmp, path)
            except BaseException:
                Path(tmp).unlink(missing_ok=True)
                raise
        return StoredBlob(sha256=sha, size=len(data), path=path)

    def put_image(self, data: bytes) -> StoredImage:
        width, height, mime = image_info(data)
        blob = self.put(data)
        return StoredImage(blob.sha256, blob.size, blob.path, width=width, height=height, mime=mime)

    def put_generated(self, data: bytes) -> StoredImage:
        """Store model-generated pixels; refuses anything over the 1024 px long edge."""
        width, height, _ = image_info(data)
        if max(width, height) > MAX_GENERATED_EDGE:
            raise OversizeImageError(
                f"generated image is {width}x{height}; the long edge must be <= "
                f"{MAX_GENERATED_EDGE} px (downscale before storing)"
            )
        return self.put_image(data)

    def read(self, sha256: str) -> bytes:
        path = self.path_for(sha256)
        try:
            return path.read_bytes()
        except FileNotFoundError as exc:
            raise BlobNotFoundError(sha256) from exc
