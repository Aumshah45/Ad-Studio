"""Products: upload a reference photo, list and fetch products."""

import uuid
from typing import Annotated, Any

from fastapi import APIRouter, Depends, File, Form, Query, Request, Response, UploadFile, status
from sqlalchemy.ext.asyncio import AsyncSession

from backend.core.errors import AppError
from backend.core.settings import Settings
from backend.db import repositories as repo
from backend.db.session import get_session
from backend.domain.adstudio.products import create_product_from_upload
from backend.domain.adstudio.profile import ensure_reference_facts
from backend.domain.adstudio.schemas import Page, Product
from backend.domain.adstudio.vision import VisionClient
from backend.http.deps import get_app_settings, get_blobs, get_tenant, get_vision
from backend.http.limits import IpRateLimiter, client_ip, get_product_limiter
from backend.storage.blobs import BlobStore

router = APIRouter(prefix="/v1/products", tags=["products"])
PROBLEM: dict[str, Any] = {"content": {"application/problem+json": {}}}


@router.post(
    "",
    status_code=status.HTTP_201_CREATED,
    responses={
        200: {"model": Product, "description": "The same image was uploaded before"},
        413: PROBLEM,
        415: PROBLEM,
        422: PROBLEM,
        429: PROBLEM,
    },
)
async def create_product(
    request: Request,
    response: Response,
    name: Annotated[str, Form(min_length=1, max_length=120)],
    image: Annotated[UploadFile, File(description="PNG, JPEG or WebP, at most 10 MB")],
    session: Annotated[AsyncSession, Depends(get_session)],
    blobs: Annotated[BlobStore, Depends(get_blobs)],
    tenant_id: Annotated[str, Depends(get_tenant)],
    settings: Annotated[Settings, Depends(get_app_settings)],
    vision: Annotated[VisionClient, Depends(get_vision)],
    limiter: Annotated[IpRateLimiter, Depends(get_product_limiter)],
) -> Product:
    """Upload a product photo. Idempotent by content: the same image returns the existing product
    with 200. The client filename and content type are ignored.

    When a vision judge is configured the product profile (box, colours, label text as data) is
    extracted now and stored in `reference_facts`; otherwise it is `unverified` until a run can
    compute it."""
    ip = client_ip(request)
    slot = limiter.reserve(ip)  # 429 rate-limited: PRODUCTS_PER_HOUR_PER_IP
    try:
        data = await image.read(settings.max_upload_bytes + 1)
        result = await create_product_from_upload(
            session,
            blobs,
            tenant_id,
            name=name.strip() or "Product",
            data=data,
            max_bytes=settings.max_upload_bytes,
        )
        await ensure_reference_facts(session, result.product, result.data, vision)
    except BaseException:
        limiter.release(ip, slot)  # a rejected upload does not use up the hour
        raise
    limiter.bind(slot)
    if not result.created:
        response.status_code = status.HTTP_200_OK
    return Product.from_rows(result.product, result.image)


@router.get("")
async def list_products(
    session: Annotated[AsyncSession, Depends(get_session)],
    tenant_id: Annotated[str, Depends(get_tenant)],
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
    cursor: Annotated[str | None, Query(max_length=512)] = None,
) -> Page[Product]:
    page = await repo.list_products(session, tenant_id, limit=limit, cursor=cursor)
    items: list[Product] = []
    for product in page.items:
        image = await repo.get_image(session, tenant_id, product.image_id)
        if image is not None:
            items.append(Product.from_rows(product, image))
    return Page[Product](items=items, next_cursor=page.next_cursor)


@router.get("/{product_id}", responses={404: PROBLEM})
async def get_product(
    product_id: uuid.UUID,
    session: Annotated[AsyncSession, Depends(get_session)],
    tenant_id: Annotated[str, Depends(get_tenant)],
) -> Product:
    product = await repo.get_product(session, tenant_id, product_id)
    image = await repo.get_image(session, tenant_id, product.image_id) if product else None
    if product is None or image is None:
        raise AppError(404, "not-found", "Product not found")
    return Product.from_rows(product, image)
