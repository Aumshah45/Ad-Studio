from backend.domain.adstudio.locale_data import (
    country_table,
    cue_table,
    iso_countries,
    locale_policy_table,
    season_table,
)


def test_locale_tables_load_and_are_consistent() -> None:
    iso = iso_countries()
    assert len(iso) == 249 and iso["NO"] == "Norway"
    countries = country_table()
    assert set(countries.countries) <= set(iso)
    for code, row in countries.countries.items():
        if row.climate == "tropical":
            assert row.wet, code  # tropical markets need wet months for wet/dry resolution
    seasons = season_table()
    assert sorted(seasons.months) == list(range(1, 13))
    policy = locale_policy_table()
    assert set(policy.countries) <= set(iso)
    assert set(policy.holidays) <= set(seasons.holidays)
    assert set(cue_table().seasons) >= {"summer", "winter", "tropical_wet", "hot", "unspecified"}
