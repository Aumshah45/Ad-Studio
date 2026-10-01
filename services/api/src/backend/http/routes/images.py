"""Image bytes by id: immutable, content-addressed, tenant-scoped."""

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, Header, Response
from sqlalchemy.ext.asyncio import AsyncSession

from backend.core.errors import AppError
from backend.db import repositories as repo
from backend.db.session import get_session
from backend.http.deps import get_blobs, get_tenant
from backend.storage.blobs import BlobNotFoundError, BlobStore

router = APIRouter(prefix="/v1/images", tags=["images"])
IMMUTABLE = "private, max-age=31536000, immutable"


@router.get(
    "/{image_id}",
    response_class=Response,
    responses={
        200: {
            "content": {"image/png": {}, "image/jpeg": {}, "image/webp": {}},
            "description": "Image bytes",
        },
        304: {"description": "Not modified (ETag matched)"},
        404: {"content": {"application/problem+json": {}}},
    },
)
async def get_image(
    image_id: uuid.UUID,
    session: Annotated[AsyncSession, Depends(get_session)],
    blobs: Annotated[BlobStore, Depends(get_blobs)],
    tenant_id: Annotated[str, Depends(get_tenant)],
    if_none_match: Annotated[str | None, Header()] = None,
) -> Response:
    image = await repo.get_image(session, tenant_id, image_id)
    if image is None:
        raise AppError(404, "not-found", "Image not found")
    etag = f'"{image.sha256}"'
    # X-Content-Type-Options: nosniff comes from SecurityHeadersMiddleware on every response.
    headers = {
        "ETag": etag,
        "Cache-Control": IMMUTABLE,
        "Content-Security-Policy": "default-src 'none'; frame-ancestors 'none'",
        "Content-Disposition": "inline",
    }
    if if_none_match and etag in {t.strip() for t in if_none_match.split(",")}:
        return Response(status_code=304, headers=headers)
    try:
        data = blobs.read(image.sha256)
    except BlobNotFoundError as exc:
        raise AppError(404, "not-found", "Image not found", "The image bytes are missing.") from exc
    return Response(content=data, media_type=image.mime, headers=headers)
