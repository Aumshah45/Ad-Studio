"""Golden dataset provenance (read-only): `data/golden/SOURCES.md` and its product table."""

from typing import Any

from fastapi import APIRouter
from pydantic import BaseModel, Field

from backend.core.errors import AppError
from backend.golden.dataset import GoldenPaths
from backend.golden.sources import ProductSource, read_sources
from backend.http.routes.evals import golden_briefs

router = APIRouter(prefix="/v1/golden", tags=["golden"])
PROBLEM: dict[str, Any] = {"content": {"application/problem+json": {}}}


class GoldenProductSource(ProductSource):
    name: str | None = Field(default=None, description="Product name from briefs.yaml")


class GoldenSources(BaseModel):
    path: str = Field(description="Repository path of the sources file")
    markdown: str = Field(description="SOURCES.md verbatim (render as markdown)")
    products: list[GoldenProductSource]


@router.get("/sources", responses={404: PROBLEM})
async def golden_sources() -> GoldenSources:
    """Where every golden reference photo comes from, its author and licence (SOURCES.md), plus
    the product table parsed into rows."""
    path = GoldenPaths().source / "SOURCES.md"
    try:
        markdown, rows = read_sources(path)
    except OSError as exc:
        raise AppError(404, "not-found", "No golden SOURCES.md") from exc
    golden = golden_briefs()
    products = [
        GoldenProductSource(
            **row.model_dump(),
            name=golden.products[row.id].name if golden and row.id in golden.products else None,
        )
        for row in rows
    ]
    return GoldenSources(path="data/golden/SOURCES.md", markdown=markdown, products=products)
