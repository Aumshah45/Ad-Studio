from typing import Literal

from pydantic import BaseModel

from backend.http.sse import StatusEvent, format_event


class _SeqEvent(BaseModel):
    type: Literal["run.status"] = "run.status"
    seq: int
    status: str


def test_format_event_emits_id_for_sequenced_events() -> None:
    frame = format_event(_SeqEvent(seq=7, status="planning"))
    assert frame.startswith("id: 7\nevent: run.status\ndata: {")
    assert frame.endswith("\n\n")


def test_format_event_without_seq_has_no_id() -> None:
    assert format_event(StatusEvent(message="hi")).startswith("event: status\ndata: ")
