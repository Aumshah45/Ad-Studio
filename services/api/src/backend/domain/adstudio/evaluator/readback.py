"""VLM blind read-back of the ad's text (ai-design §5.2, reconciliation).

The reader is the `ad_inspect` vision call's blind transcription (`StaticReadback`; the judge is
never told the required text). VLMs autocorrect ("Summr" -> "Summer"), so a VLM "match" never
passes text on its own. From ev-0.8 the read-back is the third reader of the text ensemble
(`text.py`): its headline errors and stray reads count when an OCR engine agrees with them.
`NoReadback` adds nothing.
"""

from typing import Protocol

from pydantic import BaseModel, Field
from rapidfuzz import fuzz

from backend.domain.adstudio.layout import NormBox
from backend.domain.adstudio.textnorm import compact, delimiters_to_keep, normalise


class TextRead(BaseModel):
    text: str
    box_2d: list[int] | None = Field(
        default=None, description="[ymin, xmin, ymax, xmax] normalised 0-1000 (Gemini convention)"
    )


class TextReadback(Protocol):
    async def read(self, image: bytes) -> list[TextRead] | None:
        """Every line of text the model sees, transcribed without correction; None = not run."""
        ...


class NoReadback:
    async def read(self, image: bytes) -> list[TextRead] | None:
        return None


class StaticReadback:
    """Reads already obtained elsewhere (the `ad_inspect` call's blind transcription)."""

    def __init__(self, reads: list[TextRead]) -> None:
        self.reads = reads

    async def read(self, image: bytes) -> list[TextRead] | None:
        return self.reads


def _in_zone(read: TextRead, zone: NormBox) -> bool:
    if read.box_2d is None or len(read.box_2d) != 4:
        return True
    ymin, xmin, ymax, xmax = (v / 1000 for v in read.box_2d)
    cx, cy = (xmin + xmax) / 2, (ymin + ymax) / 2
    return zone.x0 <= cx <= zone.x1 and zone.y0 <= cy <= zone.y1


def _has_box(read: TextRead) -> bool:
    return read.box_2d is not None and len(read.box_2d) == 4


def _on_product(read: TextRead, box: NormBox) -> bool:
    """The read sits on the product: its centre inside the (grown) product box, or at least half
    of its area overlapping it (a long label line whose centre falls just outside)."""
    if _in_zone(read, box):
        return True
    if read.box_2d is None or len(read.box_2d) != 4:
        return False
    ymin, xmin, ymax, xmax = (v / 1000 for v in read.box_2d)
    area = max(0.0, xmax - xmin) * max(0.0, ymax - ymin)
    if area <= 0:
        return False
    ix = max(0.0, min(xmax, box.x1) - max(xmin, box.x0))
    iy = max(0.0, min(ymax, box.y1) - max(ymin, box.y0))
    return ix * iy >= 0.5 * area


def _label_text(text: str, labels: list[str], ratio: float) -> bool:
    """The read is (part of) the product's own printed label text (reference facts)."""
    squeezed = text.replace(" ", "")
    for label in labels:
        if fuzz.partial_ratio(text, label) >= ratio:
            return True
        # OCR-like spacing and symbol differences ("SUN BUM" vs "Sun BUM®")
        if len(squeezed) >= 3 and squeezed in label.replace(" ", ""):
            return True
    return False


def zone_text(reads: list[TextRead], zone: NormBox) -> str:
    """Every read whose centre lies in the headline zone (with its 5% margin), in order."""
    margin_zone = zone.expanded(0.05)
    return " ".join(r.text for r in reads if _in_zone(r, margin_zone))


def readback_matches(reads: list[TextRead], required_text: str, script: str, zone: NormBox) -> bool:
    """The blind read-back of the zone equals the copy exactly (quote/guillemet/bracket
    characters the copy lacks count). Used only to ask for an OCR re-read, never to pass text."""
    keep = delimiters_to_keep(required_text)
    in_zone = zone_text(reads, zone)
    return bool(in_zone) and compact(in_zone, script, keep) == compact(required_text, script)


def vlm_strays(
    reads: list[TextRead],
    zone: NormBox,
    exclude: list[NormBox],
    reference_labels: tuple[str, ...],
    min_alnum: int,
    label_ratio: float,
    box_margin: float = 0.0,
) -> list[TextRead]:
    """Reads with a box outside the zone margin and not on any product box that are not the
    product's own label text. A read without a box is never stray (it may be the headline).
    ev-0.7 (R5): product boxes grow by `box_margin` and a read overlapping one by half its area
    counts as on it; label text also matches with spacing and symbols ignored."""
    labels = [normalise(t) for t in reference_labels if normalise(t)]
    stray: list[TextRead] = []
    grown = [box.expanded(box_margin) for box in exclude]
    for read in reads:
        if not _has_box(read) or _in_zone(read, zone):
            continue
        if any(_on_product(read, box) for box in grown):
            continue
        text = normalise(read.text)
        if sum(ch.isalnum() for ch in text) < min_alnum:
            continue
        if _label_text(text, labels, label_ratio):
            continue
        stray.append(read)
    return stray
