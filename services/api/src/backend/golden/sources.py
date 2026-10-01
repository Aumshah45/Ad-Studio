"""`data/golden/SOURCES.md`: product photo provenance and licences, parsed for the API.

The file is the source of truth (ADR-004); this only reads the "Reference product images" table
(`| id | File | Role | Source | Author | Licence |`, with markdown links in Source and Licence).
"""

import re
from pathlib import Path

from pydantic import BaseModel, Field

_LINK = re.compile(r"\[(?P<text>[^\]]*)\]\((?P<url>[^)\s]+)\)")
_HEADER = ("id", "file", "role", "source", "author", "licence")


class ProductSource(BaseModel):
    id: str = Field(description="Golden product key (P1..P5)")
    file: str
    role: str
    source_title: str
    source_url: str | None
    author: str
    licence: str
    licence_url: str | None
    share_alike: bool


def _cell_link(cell: str) -> tuple[str, str | None]:
    match = _LINK.search(cell)
    if match is None:
        return cell.strip(), None
    return match.group("text").strip(), match.group("url")


def _cells(line: str) -> list[str]:
    return [c.strip() for c in line.strip().strip("|").split("|")]


def parse_sources(markdown: str) -> list[ProductSource]:
    """Rows of the first table whose header is id | File | Role | Source | Author | Licence."""
    out: list[ProductSource] = []
    in_table = False
    for line in markdown.splitlines():
        if not line.strip().startswith("|"):
            if in_table:
                break
            continue
        cells = _cells(line)
        head = tuple(c.lower().split(" ")[0] for c in cells)
        if not in_table:
            in_table = head[: len(_HEADER)] == _HEADER
            continue
        if all(set(c) <= set("-: ") for c in cells) or len(cells) < len(_HEADER):
            continue
        pid, file, role, source, author, licence = cells[: len(_HEADER)]
        title, url = _cell_link(source)
        lic, lic_url = _cell_link(licence)
        out.append(
            ProductSource(
                id=pid,
                file=file.strip("`"),
                role=role,
                source_title=title,
                source_url=url,
                author=author,
                licence=lic,
                licence_url=lic_url,
                share_alike="BY-SA" in lic.upper(),
            )
        )
    return out


def read_sources(path: Path) -> tuple[str, list[ProductSource]]:
    markdown = path.read_text(encoding="utf-8")
    return markdown, parse_sources(markdown)
