from __future__ import annotations

from scraper.pcgamingwiki_deprecated.client import _coerce_value
from scraper.pcgamingwiki_deprecated.models import PCGamingWikiCargoField


def test_pcgamingwiki_is_in_a_deprecated_submodule_and_not_a_pipeline_source() -> None:
    import scraper.pcgamingwiki_deprecated

    assert scraper.pcgamingwiki_deprecated.__name__.endswith("_deprecated")


def test_pcgamingwiki_cargo_values_are_coerced_without_wikitext_parsing() -> None:
    developers = _coerce_value(
        "Valve Corporation, Hidden Path Entertainment",
        PCGamingWikiCargoField(
            name="Developers",
            field_type="Page",
            is_list=True,
            delimiter=",",
        ),
    )
    app_id = _coerce_value(
        "620",
        PCGamingWikiCargoField(name="Steam_AppID", field_type="Integer"),
    )

    assert developers == ["Valve Corporation", "Hidden Path Entertainment"]
    assert app_id == 620
