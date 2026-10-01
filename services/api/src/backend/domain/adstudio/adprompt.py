"""Image-prompt fields from the spec plus the product profile (shared by pipeline and repairer)."""

from dataclasses import replace
from typing import Literal

from backend.domain.adstudio.planner import ReferenceFacts
from backend.domain.adstudio.prompting import AdPromptFields
from backend.domain.adstudio.spec import CreativeSpec


def prompt_fields(
    spec: CreativeSpec,
    facts: ReferenceFacts | None,
    *,
    text_mode: Literal["native", "overlay"] | None = None,
) -> AdPromptFields:
    """The spec's prompt fields plus the product profile (category, colours, label text as data).

    `text_mode="overlay"` asks for a clean plate (empty headline zone) whatever the spec says.
    """
    fields = spec.prompt_fields()
    if text_mode is not None:
        fields = replace(fields, text_mode=text_mode)
    if facts is None or not facts.category:
        return fields
    return replace(
        fields,
        category=facts.category,
        dominant_colors=list(facts.dominant_colors),
        visible_text=list(facts.visible_text),
    )
