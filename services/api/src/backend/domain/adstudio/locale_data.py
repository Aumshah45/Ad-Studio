"""Typed loaders for the planner's code tables under `data/` (validated once, cached)."""

from functools import lru_cache
from pathlib import Path
from typing import Any, Literal, cast

import yaml
from pydantic import BaseModel, Field, field_validator

DATA = Path(__file__).resolve().parent / "data"

Climate = Literal["temperate", "mediterranean", "continental", "polar", "tropical", "arid"]
Hemisphere = Literal["north", "south", "equatorial"]
EffectiveSeason = Literal[
    "spring",
    "summer",
    "autumn",
    "winter",
    "tropical_wet",
    "tropical_dry",
    "hot",
    "mild",
    "unspecified",
]
HEX = r"^#[0-9A-Fa-f]{6}$"


class CountryRow(BaseModel):
    lat: float = Field(ge=-90, le=90)
    climate: Climate
    hemisphere: Hemisphere | None = None
    wet: list[int] = Field(default_factory=list[int])
    currency: str
    script: str
    lang: list[str] = Field(default_factory=list[str])
    cities: list[str] = Field(default_factory=list[str])
    cues: list[str] = Field(default_factory=list[str])

    @field_validator("wet")
    @classmethod
    def _months(cls, v: list[int]) -> list[int]:
        if any(m < 1 or m > 12 for m in v):
            raise ValueError("wet months must be 1..12")
        return v

    @property
    def resolved_hemisphere(self) -> Hemisphere:
        if self.hemisphere is not None:
            return self.hemisphere
        if abs(self.lat) < 10:
            return "equatorial"
        return "north" if self.lat > 0 else "south"


class CountryTable(BaseModel):
    version: int
    countries: dict[str, CountryRow]
    aliases: dict[str, str]


class HolidayRow(BaseModel):
    name: str
    aliases: list[str]
    months: list[int]
    by_country: dict[str, list[int]] = Field(default_factory=dict[str, list[int]])
    hemisphere_months: dict[str, list[int]] = Field(default_factory=dict[str, list[int]])
    cues: list[str] = Field(default_factory=list[str])


class SeasonTable(BaseModel):
    version: int
    months: dict[int, list[str]]
    named_seasons: dict[EffectiveSeason, list[str]]
    holidays: dict[str, HolidayRow]


class PolicyBlock(BaseModel):
    avoid: list[str] = Field(default_factory=list[str])
    notes: str | None = None


class LocalePolicyTable(BaseModel):
    version: int
    default: PolicyBlock
    conservative: PolicyBlock
    countries: dict[str, PolicyBlock] = Field(default_factory=dict[str, PolicyBlock])
    holidays: dict[str, PolicyBlock] = Field(default_factory=dict[str, PolicyBlock])


class CueRow(BaseModel):
    setting: str
    season_cues: list[str]
    contradiction_cues: list[str]
    palette: list[str]
    lighting: str
    mood: str


class CueTable(BaseModel):
    version: int
    seasons: dict[EffectiveSeason, CueRow]
    generic_locale_cues: list[str]


def _load(name: str) -> dict[str, Any]:
    return cast(dict[str, Any], yaml.safe_load((DATA / name).read_text(encoding="utf-8")))


@lru_cache(maxsize=1)
def iso_countries() -> dict[str, str]:
    raw = _load("iso3166.yaml")
    return {str(k).upper(): str(v) for k, v in raw.items()}


@lru_cache(maxsize=1)
def country_table() -> CountryTable:
    table = CountryTable.model_validate(_load("countries.yaml"))
    iso = iso_countries()
    unknown = [c for c in [*table.countries, *table.aliases.values()] if c not in iso]
    if unknown:
        raise ValueError(f"countries.yaml uses non-ISO codes: {unknown}")
    return table


@lru_cache(maxsize=1)
def season_table() -> SeasonTable:
    return SeasonTable.model_validate(_load("seasons.yaml"))


@lru_cache(maxsize=1)
def locale_policy_table() -> LocalePolicyTable:
    return LocalePolicyTable.model_validate(_load("locale_policy.yaml"))


@lru_cache(maxsize=1)
def cue_table() -> CueTable:
    return CueTable.model_validate(_load("cue_defaults.yaml"))
