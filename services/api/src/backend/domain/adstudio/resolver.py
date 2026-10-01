"""Deterministic geography and season resolution (ai-design §2.1, ADR-006).

The hemisphere and effective season come from code tables, never from a model, so the 12 cases in
ai-design §9.6 are correct by construction. Only an unrecognised season term may go to a model
(`season_resolve`, on wrapped untrusted text) and the model can only pick months, a named season or
a holiday id from our tables; anything else abstains (422 `unknown-season`).
"""

import re
import unicodedata
from typing import Literal

from pydantic import BaseModel, Field

from backend.core.errors import AppError
from backend.domain.adstudio.locale_data import (
    Climate,
    EffectiveSeason,
    HolidayRow,
    country_table,
    iso_countries,
    season_table,
)

HemisphereOrUnknown = Literal["north", "south", "equatorial", "unknown"]
ClimateOrUnknown = Climate | Literal["unknown"]
MONTH_NAMES = (
    "January",
    "February",
    "March",
    "April",
    "May",
    "June",
    "July",
    "August",
    "September",
    "October",
    "November",
    "December",
)
_SEASON_MONTHS: dict[str, dict[str, list[int]]] = {
    "north": {
        "winter": [12, 1, 2],
        "spring": [3, 4, 5],
        "summer": [6, 7, 8],
        "autumn": [9, 10, 11],
    },
    "south": {
        "summer": [12, 1, 2],
        "autumn": [3, 4, 5],
        "winter": [6, 7, 8],
        "spring": [9, 10, 11],
    },
}
ACCEPTED_FORMATS = (
    "a month (e.g. December, Dec, 12), a season (summer, winter, spring, autumn/fall, wet or dry "
    "season) or a holiday (e.g. Christmas, Diwali, Lunar New Year, Black Friday)"
)


class GeoResolution(BaseModel):
    country_code: str
    country_name: str
    latitude: float | None
    hemisphere: HemisphereOrUnknown
    climate: ClimateOrUnknown
    wet_months: list[int] = Field(default_factory=list[int])
    currency_symbol: str | None = None
    script: str | None = None
    ocr_langs: list[str] = Field(default_factory=list[str])
    city: str | None = None
    locale_cues: list[str] = Field(default_factory=list[str])
    known: bool = Field(description="False: ISO code without market data (conservative policy)")
    source: Literal["table", "iso_list"]


class SeasonResolution(BaseModel):
    input: str
    input_kind: Literal["month", "named_season", "holiday"]
    months: list[int]
    effective_season: EffectiveSeason
    holidays: list[str] = Field(default_factory=list[str])
    holiday_ids: list[str] = Field(default_factory=list[str])
    rationale: str
    source: Literal["table", "model"] = "table"


def fold(text: str) -> str:
    """Lower-case, accent-folded, punctuation-light form used for table lookups."""
    decomposed = unicodedata.normalize("NFKD", text.casefold())
    no_marks = "".join(ch for ch in decomposed if not unicodedata.combining(ch))
    cleaned = re.sub(r"[^\w'.\- ]+", " ", no_marks.replace("’", "'"))
    return re.sub(r"\s+", " ", cleaned).strip()


# --- geography -----------------------------------------------------------------------------------


def unknown_geography(value: str) -> AppError:
    return AppError(
        422,
        "unknown-geography",
        "Unknown geography",
        f"{value!r} is not an ISO 3166-1 alpha-2 country code.",
    )


def lookup_country(value: str) -> str | None:
    """ISO code for a code, an English country name, an alias or a known city; else None."""
    raw = value.strip()
    iso = iso_countries()
    if len(raw) == 2 and raw.upper() in iso:
        return raw.upper()
    key = fold(raw)
    table = country_table()
    if key in table.aliases:
        return table.aliases[key]
    for code, name in iso.items():
        if fold(name) == key:
            return code
    for code, row in table.countries.items():
        if key in (fold(c) for c in row.cities):
            return code
    return None


def resolve_geography(code: str, detail: str | None = None) -> GeoResolution:
    """Resolve an ISO code (+ optional city/region from the alias table only).

    A detail that belongs to another country is a 422; an unknown detail is ignored (it is free
    text and never reaches a model).
    """
    cc = code.strip().upper()
    iso = iso_countries()
    if cc not in iso:
        raise unknown_geography(code)
    city: str | None = None
    if detail and detail.strip():
        found = lookup_country(detail)
        if found is not None and found != cc and fold(detail) != fold(iso[cc]):
            raise AppError(
                422,
                "geography-mismatch",
                "Geography mismatch",
                f"{detail.strip()!r} is in {iso[found]}, not {iso[cc]}.",
            )
        row = country_table().countries.get(cc)
        if found == cc and row is not None and fold(detail) in (fold(c) for c in row.cities):
            city = detail.strip().title()
    row = country_table().countries.get(cc)
    if row is None:
        return GeoResolution(
            country_code=cc,
            country_name=iso[cc],
            latitude=None,
            hemisphere="unknown",
            climate="unknown",
            known=False,
            source="iso_list",
        )
    return GeoResolution(
        country_code=cc,
        country_name=iso[cc],
        latitude=row.lat,
        hemisphere=row.resolved_hemisphere,
        climate=row.climate,
        wet_months=row.wet,
        currency_symbol=row.currency,
        script=row.script,
        ocr_langs=row.lang,
        city=city,
        locale_cues=row.cues,
        known=True,
        source="table",
    )


# --- seasons -------------------------------------------------------------------------------------

_QUALIFIER = re.compile(
    r"^(early|mid|late|end of|beginning of|start of|middle of|the|in)[\s-]+", re.IGNORECASE
)
_YEAR = re.compile(r"\b(19|20)\d{2}\b")


def _clean_season(text: str) -> str:
    key = fold(_YEAR.sub(" ", text))
    previous = None
    while previous != key:
        previous = key
        key = _QUALIFIER.sub("", key).strip(" -")
    return key


def month_text(months: list[int]) -> str:
    if not months:
        return "no fixed month"
    return ", ".join(MONTH_NAMES[m - 1] for m in months)


def _season_for_month(month: int, geo: GeoResolution) -> tuple[EffectiveSeason, str]:
    name = MONTH_NAMES[month - 1]
    where = geo.country_name
    if geo.climate == "tropical":
        wet = month in geo.wet_months
        season: EffectiveSeason = "tropical_wet" if wet else "tropical_dry"
        return season, (
            f"{name} in {where} (tropical, wet season {month_text(geo.wet_months)}) is the "
            f"{'wet' if wet else 'dry'} season"
        )
    if geo.climate == "arid":
        hot = {5, 6, 7, 8, 9} if geo.hemisphere != "south" else {11, 12, 1, 2, 3}
        season = "hot" if month in hot else "mild"
        return season, f"{name} in {where} (arid climate) is the {season} season"
    if geo.hemisphere in ("north", "south"):
        for season_name, months in _SEASON_MONTHS[geo.hemisphere].items():
            if month in months:
                s: EffectiveSeason = season_name  # type: ignore[assignment]
                return s, f"{name} in the {geo.hemisphere}ern hemisphere ({where}) is {season_name}"
    return "unspecified", (
        f"No climate data for {where}; the season for {name} is left unspecified"
    )


def _named_months(season: EffectiveSeason, geo: GeoResolution) -> list[int]:
    if season in ("tropical_wet", "tropical_dry"):
        if not geo.wet_months:
            return []
        wet = sorted(geo.wet_months)
        return wet if season == "tropical_wet" else [m for m in range(1, 13) if m not in wet]
    hemisphere = geo.hemisphere if geo.hemisphere in ("north", "south") else None
    if hemisphere is None:
        return []
    return _SEASON_MONTHS[hemisphere].get(season, [])


def holiday_months(row: HolidayRow, geo: GeoResolution) -> list[int]:
    if geo.country_code in row.by_country:
        return row.by_country[geo.country_code]
    if geo.hemisphere in row.hemisphere_months:
        return row.hemisphere_months[geo.hemisphere]
    return row.months


def season_from_months(
    text: str,
    months: list[int],
    geo: GeoResolution,
    *,
    kind: Literal["month", "holiday"],
    holiday: tuple[str, HolidayRow] | None = None,
    source: Literal["table", "model"] = "table",
) -> SeasonResolution:
    holidays = [holiday[1].name] if holiday else []
    holiday_ids = [holiday[0]] if holiday else []
    if not months:
        what = holiday[1].name if holiday else text
        return SeasonResolution(
            input=text,
            input_kind=kind,
            months=[],
            effective_season="unspecified",
            holidays=holidays,
            holiday_ids=holiday_ids,
            rationale=f"{what} has no fixed month, so the season is unspecified",
            source=source,
        )
    season, why = _season_for_month(months[0], geo)
    if holiday:
        why = f"{holiday[1].name} falls in {month_text(months)}; {why}"
        if season in ("summer", "winter") and holiday[0] == "christmas":
            why += f" (a {season} Christmas)"
    return SeasonResolution(
        input=text,
        input_kind=kind,
        months=months,
        effective_season=season,
        holidays=holidays,
        holiday_ids=holiday_ids,
        rationale=why,
        source=source,
    )


def named_season(
    text: str, season: EffectiveSeason, geo: GeoResolution, *, source: Literal["table", "model"]
) -> SeasonResolution:
    months = _named_months(season, geo)
    label = season.replace("_", " ")
    if months:
        why = f"{label.capitalize()} is taken as the local season in {geo.country_name}: " + (
            f"{month_text(months)} in the {geo.hemisphere}ern hemisphere"
            if geo.hemisphere in ("north", "south")
            else month_text(months)
        )
    else:
        why = f"{label.capitalize()} is taken as given; no local months for {geo.country_name}"
    return SeasonResolution(
        input=text,
        input_kind="named_season",
        months=months,
        effective_season=season,
        rationale=why,
        source=source,
    )


def resolve_season(text: str, geo: GeoResolution) -> SeasonResolution | None:
    """Table lookup only. None means "not recognised" (the caller may try the model, then 422)."""
    key = _clean_season(text)
    if not key:
        return None
    table = season_table()
    if key.isdigit() and 1 <= int(key) <= 12:
        m = int(key)
        return season_from_months(text, [m], geo, kind="month")
    for month, names in table.months.items():
        if key in (fold(n) for n in names):
            return season_from_months(text, [month], geo, kind="month")
    for season, names in table.named_seasons.items():
        if key in (fold(n) for n in names):
            return named_season(text, season, geo, source="table")
    for holiday_id, row in table.holidays.items():
        if key in (fold(a) for a in row.aliases) or key == fold(row.name):
            return season_from_months(
                text,
                holiday_months(row, geo),
                geo,
                kind="holiday",
                holiday=(holiday_id, row),
            )
    return None


def unknown_season(text: str) -> AppError:
    return AppError(
        422,
        "unknown-season",
        "Unknown season",
        f"We couldn't read {text.strip()!r} as a season. Use {ACCEPTED_FORMATS}.",
        accepted=ACCEPTED_FORMATS,
    )


def holiday_row(holiday_id: str) -> HolidayRow | None:
    return season_table().holidays.get(holiday_id)
