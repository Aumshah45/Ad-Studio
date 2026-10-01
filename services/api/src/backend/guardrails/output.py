"""Output checks on model results."""

from collections.abc import Iterable
from dataclasses import dataclass
from enum import StrEnum


class AnswerStatus(StrEnum):
    OK = "ok"
    PARTIAL = "partial"
    REFUSED = "refused"
    OUT_OF_SCOPE = "out_of_scope"
    ERROR = "error"


@dataclass(frozen=True)
class CitationCheck:
    valid: list[str]
    invalid: list[str]

    @property
    def ok(self) -> bool:
        return not self.invalid


def verify_citations(cited_ids: Iterable[str], allowed_ids: Iterable[str]) -> CitationCheck:
    allowed = set(allowed_ids)
    valid: list[str] = []
    invalid: list[str] = []
    for cid in cited_ids:
        (valid if cid in allowed else invalid).append(cid)
    return CitationCheck(valid=valid, invalid=invalid)
