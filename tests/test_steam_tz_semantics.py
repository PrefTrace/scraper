from __future__ import annotations

import json
from pathlib import Path

from scraper.steam.locales import normalize_locale
from scraper.steam.parsers import (
    normalize_release_status,
    parse_achievement_schema,
    parse_app_details,
    parse_appinfo_semantics,
    parse_bundle_membership,
    parse_descriptors,
    parse_external_links,
    parse_global_achievement_percentages,
    parse_languages_fallback,
    parse_media,
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
    parsed = parse_store_browse_item(
        _fixture("storebrowse_281990.json"),
        price_region="USD",
        bundle_memberships={21037: [121840]},
    )

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


def test_structured_achievements_keep_one_stable_id_across_locales() -> None:
    percentages = parse_global_achievement_percentages(_fixture("achievement_percentages.json"))
    english = parse_achievement_schema(
        _fixture("achievement_schema_en.json"),
        language="en-US",
        percentages=percentages,
    )
    russian = parse_achievement_schema(
        _fixture("achievement_schema_ru.json"),
        language="ru-RU",
        percentages=percentages,
    )

    assert [item.achievement_id for item in english] == ["ACH_FIRST_STEP", "ACH_SECRET"]
    assert [item.achievement_id for item in russian] == ["ACH_FIRST_STEP", "ACH_SECRET"]
    assert [item.name for item in russian] == ["Первый шаг", "Тайный путь"]
    assert [item.global_percent for item in english] == [73.5, 4.25]
    assert english[1].hidden is True


def test_release_literals_cover_all_tz_states_and_preorder_is_not_a_state() -> None:
    assert normalize_release_status({"release_state": "removed"}) == "removed"
    assert normalize_release_status({"release_state": "advanced_access"}) == "advanced_access"
    assert normalize_release_status({"is_early_access": True}) == "early_access"
    assert normalize_release_status({"release_date": {"coming_soon": True}}) == "not_released"
    assert normalize_release_status({"release_state": "preorder"}) == "not_released"
    assert normalize_release_status({"is_preorder": True}) == "not_released"
    assert normalize_release_status({"release_date": {"coming_soon": False}}) == "released"


def test_bundle_memberships_are_read_only_from_bundle_specific_response() -> None:
    app_payload = {
        "purchase_options": [
            {"bundleid": 100, "final_price_in_cents": 1000},
            {"bundleid": 200, "final_price_in_cents": 1500},
        ],
        "included_items": {
            "included_packages": [{"best_purchase_option": {"packageid": 999}}]
        },
    }
    parsed = parse_store_browse_item(app_payload)
    assert [bundle.edition_package_ids for bundle in parsed["bundles"]] == [[], []]
    bundle_100 = {
        "response": {
            "store_items": [
                {
                    "id": 100,
                    "included_items": {
                        "included_packages": [
                            {"best_purchase_option": {"packageid": 10}},
                            {"best_purchase_option": {"packageid": 11}},
                        ]
                    },
                }
            ]
        }
    }
    bundle_200 = {
        "response": {
            "store_items": [
                {
                    "id": 200,
                    "included_items": {
                        "included_packages": [{"best_purchase_option": {"packageid": 20}}]
                    },
                }
            ]
        }
    }
    assert parse_bundle_membership(bundle_100) == (100, [10, 11])
    assert parse_bundle_membership(bundle_200) == (200, [20])
    enriched = parse_store_browse_item(
        app_payload,
        bundle_memberships={100: [10, 11], 200: [20]},
    )
    assert [bundle.edition_package_ids for bundle in enriched["bundles"]] == [[10, 11], [20]]


def test_structured_links_and_asset_mapping_are_canonical() -> None:
    links = parse_external_links(
        {
            "website": "https://example.com",
            "support_info": {"url": "https://support.example", "email": "help@example.com"},
            "links": [
                {"link_type": 1, "url": "https://youtube.com/example"},
                {"link_type": 3, "url": "https://x.com/example"},
                {"link_type": 5, "url": "https://discord.gg/example"},
                {"type": "VK", "url": "https://vk.com/example"},
            ],
        }
    )
    assert {item.type for item in links} >= {
        "website",
        "support_website",
        "support_email",
        "youtube",
        "twitter",
        "discord",
        "vk",
    }
    media = parse_media(
        {
            "header_image": "https://cdn.example/header.jpg",
            "capsule_image": "https://cdn.example/capsule_231x87.jpg",
            "capsule_imagev5": "https://cdn.example/capsule_184x69.jpg",
            "capsule_imagev6": "https://cdn.example/capsule_616x353.jpg",
            "background_raw": "https://cdn.example/background.jpg",
            "screenshots": [{"id": 1, "path_full": "https://cdn.example/shot.png"}],
            "movies": [
                {
                    "id": 2,
                    "webm": {"max": "https://cdn.example/trailer.webm"},
                    "hls_h264": "https://cdn.example/trailer.m3u8",
                    "dash_h264": "https://cdn.example/trailer.mpd",
                }
            ],
        }
    )
    assert [item.media_type for item in media].count("main_capsule") == 1
    trailer = next(item for item in media if item.media_type == "trailer")
    assert trailer.url == "https://cdn.example/trailer.webm"
