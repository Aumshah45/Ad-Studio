"""Tiny in-memory image fixtures for tests (no files, no network)."""

import io

from PIL import Image


def png(width: int = 320, height: int = 320, color: tuple[int, int, int] = (200, 30, 30)) -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", (width, height), color).save(buf, format="PNG")
    return buf.getvalue()


def jpeg_with_exif(width: int = 400, height: int = 300, orientation: int = 6) -> bytes:
    """A JPEG whose EXIF carries GPS, owner name and an orientation tag."""
    img = Image.new("RGB", (width, height), (10, 120, 200))
    img.paste((250, 250, 0), (0, 0, 40, 40))  # marks the top-left corner
    exif = Image.Exif()
    exif[0x0112] = orientation  # Orientation
    exif[0x013B] = "Jane Owner"  # Artist
    exif[0xA430] = "Jane Owner"  # CameraOwnerName
    gps = exif.get_ifd(0x8825)
    gps[1], gps[2] = "N", (51.0, 30.0, 0.0)
    gps[3], gps[4] = "W", (0.0, 7.0, 0.0)
    buf = io.BytesIO()
    img.save(buf, format="JPEG", exif=exif, quality=90)
    return buf.getvalue()


def png_bomb(width: int = 20_000, height: int = 20_000) -> bytes:
    """A valid 1-bit PNG with huge dimensions that compresses to a few hundred KB at most."""
    import struct
    import zlib

    def chunk(kind: bytes, body: bytes) -> bytes:
        return (
            struct.pack(">I", len(body))
            + kind
            + body
            + struct.pack(">I", zlib.crc32(kind + body) & 0xFFFFFFFF)
        )

    row = b"\x00" + b"\x00" * ((width + 7) // 8)
    comp = zlib.compressobj(9)
    parts = [comp.compress(row) for _ in range(height)]
    idat = b"".join(parts) + comp.flush()
    ihdr = struct.pack(">IIBBBBB", width, height, 1, 0, 0, 0, 0)
    return b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", ihdr) + chunk(b"IDAT", idat) + chunk(b"IEND", b"")
