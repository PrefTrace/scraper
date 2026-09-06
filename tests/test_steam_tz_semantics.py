from __future__ import annotations

import json
from pathlib import Path

from scraper.steam.locales import normalize_locale
from scraper.steam.parsers import (
    parse_app_details,
    parse_appinfo_semantics,
    parse_descriptors,
    parse_languages_fallback,
    parse_store_browse_item,
)

FIXTURES = Path(__file__).parent / "fixtures" / "steam"


def _fixture(name: str) -> dict:
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


def test_public_appinfo_maps_tz_semantics() -> None:
    info = _fixture("appinfo_1091500.json")
    common = info["common"]
    semantics = parse_appinfo_semantics(common)

    assert semantics["deck_support"].status == "supported"
    assert {item.id for item in semantics["accessibility_features"]} == {
        64,
        65,
        66,
        67,
        68,
        69,
        70,
        74,
        78,
        79,
    }
    assert {(item.name, item.bluetooth, item.usb) for item in semantics["controllers"]} == {
        ("DualShock 4", False, True),
        ("DualSense", True, True),
    }


def test_relationships_do_not_confuse_demo_parent_with_dlc() -> None:
    main = parse_app_details(
        _fixture("appdetails_418370.json"),
        normalize_locale("en-US"),
        app_id=418370,
    )
    demo = parse_app_details(
        _fixture("appdetails_530620.json"),
        normalize_locale("en-US"),
        app_id=530620,
    )

    assert main["demo_id"] == 530620
    assert main["dlc_for_app_id"] is None
    assert demo["dlc_for_app_id"] is None


def test_appinfo_required_app_and_structured_language_flags() -> None:
    parsed = parse_app_details(
        {"name": "Portal Revolution", "type": "Game"},
        normalize_locale("en-US"),
        app_id=601360,
        app_info=_fixture("appinfo_601360.json"),
    )
    assert parsed["type"] == "game"
    assert parsed["required_app_id"] == 620

    languages = parse_languages_fallback("English, Russian")
    assert all(item.interface is None and item.subtitles is None for item in languages)


def test_content_descriptor_names_and_unknown_ids_are_stable() -> None:
    parsed = parse_descriptors({"ids": [1, 2, 5, 99]})
    assert [(item.steam_id, item.name) for item in parsed] == [
        (1, "Some Nudity or Sexual Content"),
        (2, "Frequent Violence or Gore"),
        (5, "General Mature Content"),
        (99, "unknown"),
    ]


def test_public_storebrowse_preserves_subscription_periods_and_bundle_membership() -> None:
    parsed = parse_store_browse_item(_fixture("storebrowse_281990.json"), price_region="US")

    recurring = {
        item.package_id: item
        for item in parsed["edition_prices"]
        if item.price_type == "recurring"
    }
    assert {item.period_units for item in recurring.values()} == {1, 3, 6}
    assert all(item.period == "month" for item in recurring.values())
    assert parsed["bundles"][0].bundle_id == 21037
    assert parsed["bundles"][0].edition_package_ids == [121840]
    assert parsed["bundle_prices"][0].final == 28520
