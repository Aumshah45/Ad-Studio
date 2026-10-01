import io
from typing import Any

import httpx
from PIL import Image

from tests.conftest import client_for
from tests.images import jpeg_with_exif, png, png_bomb

SVG = (
    b"<?xml version='1.0'?><svg xmlns='http://www.w3.org/2000/svg'><script>alert(1)</script></svg>"
)


async def _upload(
    client: httpx.AsyncClient, data: bytes, *, name: str = "Red mug", filename: str = "p.png"
) -> httpx.Response:
    return await client.post(
        "/v1/products", data={"name": name}, files={"image": (filename, data, "image/png")}
    )


def _problem(res: httpx.Response, status: int, type_: str) -> dict[str, Any]:
    assert res.status_code == status, res.text
    assert res.headers["content-type"].startswith("application/problem+json")
    body = res.json()
    assert body["type"] == type_ and body["status"] == status
    return body


async def test_upload_png_returns_product(app_factory) -> None:  # type: ignore[no-untyped-def]
    async with client_for(app_factory()) as client:
        res = await _upload(client, png(640, 480, (1, 2, 3)))
        assert res.status_code == 201, res.text
        body = res.json()
        assert body["name"] == "Red mug"
        # no vision judge in tests: the profile is stored as unverified (computed lazily later)
        assert body["reference_facts"]["status"] == "unverified"
        assert (body["width"], body["height"]) == (640, 480)
        assert body["image_url"] == f"/v1/images/{body['image_id']}"
        got = await client.get(f"/v1/products/{body['id']}")
        listed = await client.get("/v1/products", params={"limit": 100})
    assert got.status_code == 200 and got.json()["id"] == body["id"]
    assert body["id"] in {p["id"] for p in listed.json()["items"]}


async def test_product_idempotent_by_content(app_factory) -> None:  # type: ignore[no-untyped-def]
    data = png(512, 512, (7, 7, 7))
    async with client_for(app_factory()) as client:
        first = await _upload(client, data)
        second = await _upload(client, data, name="Another name", filename="other.jpg")
        other = await _upload(client, png(512, 512, (8, 8, 8)))
    assert (first.status_code, second.status_code, other.status_code) == (201, 200, 201)
    assert second.json()["id"] == first.json()["id"]
    assert second.json()["name"] == "Red mug"
    assert other.json()["id"] != first.json()["id"]


async def test_upload_rejects_bomb(app_factory) -> None:  # type: ignore[no-untyped-def]
    async with client_for(app_factory()) as client:
        huge = await _upload(client, png_bomb(20_000, 20_000))  # 400 MP in ~50 KB
        over_cap = await _upload(client, png_bomb(7_000, 7_000))  # 49 MP: over 40 MP, under 2x
    _problem(huge, 413, "payload-too-large")
    _problem(over_cap, 413, "payload-too-large")


async def test_upload_rejects_svg(app_factory) -> None:  # type: ignore[no-untyped-def]
    polyglot_html = b"<!DOCTYPE html><html><body><img src=x onerror=alert(1)></body></html>"
    gif = io.BytesIO()
    Image.new("RGB", (300, 300)).save(gif, format="GIF")
    async with client_for(app_factory()) as client:
        for data in (SVG, polyglot_html, gif.getvalue()):
            _problem(
                await _upload(client, data, filename="evil.png"), 415, "unsupported-media-type"
            )


async def test_upload_strips_exif(app_factory) -> None:  # type: ignore[no-untyped-def]
    async with client_for(app_factory()) as client:
        res = await _upload(client, jpeg_with_exif(400, 300, orientation=6))
        assert res.status_code == 201, res.text
        img_res = await client.get(res.json()["image_url"])
    assert img_res.headers["content-type"] == "image/png"
    img = Image.open(io.BytesIO(img_res.content))
    assert img.format == "PNG"
    assert img.size == (300, 400)  # orientation 6 (rotate 90 CW) applied, then dropped
    assert len(img.getexif()) == 0
    assert not {"exif", "icc_profile", "XML:com.adobe.xmp"} & set(img.info)
    assert b"Jane Owner" not in img_res.content and b"GPS" not in img_res.content
    # The yellow top-left marker ends up top-right after a 90 degree clockwise rotation.
    rgb = img.convert("RGB")
    corner = rgb.getpixel((295, 5))
    assert isinstance(corner, tuple) and corner[0] > 200 and corner[1] > 200 and corner[2] < 80


async def test_upload_rejects_small_animated_truncated_oversize(app_factory) -> None:  # type: ignore[no-untyped-def]
    frames = [Image.new("RGB", (300, 300), c) for c in ((255, 0, 0), (0, 255, 0))]
    anim = io.BytesIO()
    frames[0].save(anim, format="PNG", save_all=True, append_images=frames[1:])
    truncated = png(600, 600)[:-400]
    async with client_for(app_factory(max_upload_bytes=50_000)) as client:
        small = await _upload(client, png(200, 800))
        animated = await _upload(client, anim.getvalue())
        broken = await _upload(client, truncated)
        too_big = await _upload(client, b"\x89PNG\r\n\x1a\n" + b"\x00" * 60_000)
    assert _problem(small, 422, "validation-error")["reason"] == "too-small"
    assert _problem(animated, 422, "validation-error")["reason"] == "animated"
    assert _problem(broken, 422, "validation-error")["reason"] == "corrupt"
    _problem(too_big, 413, "payload-too-large")


async def test_upload_cmyk_normalised_to_srgb_png(app_factory) -> None:  # type: ignore[no-untyped-def]
    buf = io.BytesIO()
    Image.new("CMYK", (300, 300), (0, 255, 255, 0)).save(buf, format="JPEG")
    async with client_for(app_factory()) as client:
        res = await _upload(client, buf.getvalue())
        img_res = await client.get(res.json()["image_url"])
    img = Image.open(io.BytesIO(img_res.content))
    assert img.mode == "RGB" and "icc_profile" not in img.info


async def test_images_route_headers(app_factory) -> None:  # type: ignore[no-untyped-def]
    async with client_for(app_factory()) as client:
        created = (await _upload(client, png(300, 300, (4, 5, 6)))).json()
        res = await client.get(created["image_url"])
        etag = res.headers["etag"]
        cached = await client.get(created["image_url"], headers={"If-None-Match": etag})
        missing = await client.get("/v1/images/00000000-0000-0000-0000-000000000000")
        bad = await client.get("/v1/images/..%2F..%2Fetc%2Fpasswd")
    assert res.status_code == 200 and res.headers["content-type"] == "image/png"
    assert res.headers["cache-control"] == "private, max-age=31536000, immutable"
    assert res.headers["x-content-type-options"] == "nosniff"
    assert "default-src 'none'" in res.headers["content-security-policy"]
    assert len(etag.strip('"')) == 64
    assert cached.status_code == 304 and not cached.content
    _problem(missing, 404, "not-found")
    assert bad.status_code in (404, 422)
