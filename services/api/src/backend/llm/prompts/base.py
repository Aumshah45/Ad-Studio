"""Versioned prompts. Bump `version` whenever the text changes (it is part of the cache key)."""

from dataclasses import dataclass, field


@dataclass(frozen=True)
class Prompt:
    name: str
    version: str
    system: str
    few_shots: tuple[tuple[str, str], ...] = field(default=())

    def instructions(self, *extra: str) -> str:
        parts = [self.system, *extra]
        for i, (user, assistant) in enumerate(self.few_shots, start=1):
            parts.append(f"Example {i}\nInput: {user}\nOutput: {assistant}")
        return "\n\n".join(p for p in parts if p)
