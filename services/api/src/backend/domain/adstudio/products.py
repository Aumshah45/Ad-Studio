"""Product creation from an upload: validate + normalise, store the blob, idempotent by content."""

from dataclasses import dataclass

from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from backend.db import models_adstudio as m
from backend.db import repositories as repo
from backend.domain.adstudio.uploads import NormalizedImage, normalize_upload
from backend.storage.blobs import BlobStore, sha256_hex


@dataclass(frozen=True)
class ProductResult:
    product: m.Product
    image: m.Image
    created: bool
    data: bytes = b""  # the normalised PNG (what the blob holds)


async def _existing(
    session: AsyncSession, tenant_id: str, sha: str
) -> tuple[m.Product, m.Image] | None:
    image = await repo.get_image_by_sha(session, tenant_id, sha)
    if image is None:
        return None
    product = await repo.get_product_by_image(session, tenant_id, image.id)
    return (product, image) if product is not None else None


async def create_product_from_upload(
    session: AsyncSession,
    blobs: BlobStore,
    tenant_id: str,
    *,
    name: str,
    data: bytes,
    max_bytes: int,
) -> ProductResult:
    """The same image content returns the existing product (created=False).

    Identity is the sha256 of the normalised PNG, which is deterministic for the same upload.
    `reference_facts` stays null here; the route fills it with the vision product profile.
    """
    normalized: NormalizedImage = await normalize_upload(data, max_bytes=max_bytes)
    sha = sha256_hex(normalized.data)
    found = await _existing(session, tenant_id, sha)
    if found is not None:
        return ProductResult(found[0], found[1], created=False, data=normalized.data)

    stored = blobs.put(normalized.data)
    try:
        image, _ = await repo.get_or_create_image(
            session,
            tenant_id,
            sha256=stored.sha256,
            source="upload",
            mime="image/png",
            width=normalized.width,
            height=normalized.height,
            size=stored.size,
        )
        product = await repo.create_product(session, tenant_id, name=name, image_id=image.id)
        await session.commit()
    except IntegrityError:
        # A concurrent upload of the same bytes won the race; return its product.
        await session.rollback()
        found = await _existing(session, tenant_id, sha)
        if found is None:
            raise
        return ProductResult(found[0], found[1], created=False, data=normalized.data)
    return ProductResult(product, image, created=True, data=normalized.data)
