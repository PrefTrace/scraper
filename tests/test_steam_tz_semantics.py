from __future__ import annotations

import json
from pathlib import Path

from scraper.steam.locales import normalize_locale
from scraper.steam.parsers import (
    annotate_package_price_observations,
    normalize_release_status,
    parse_achievement_schema,
    parse_app_details,
    parse_appinfo_semantics,
    parse_build_branches,
    parse_bundle_membership,
    parse_depots,
    parse_descriptors,
    parse_external_links,
    parse_global_achievement_percentages,
    parse_languages_fallback,
    parse_media,
    parse_store_browse_item,
    parse_workshop_stats,
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
        (99, None),
    ]


def test_public_storebrowse_preserves_subscription_periods_and_bundle_membership() -> None:
    parsed = parse_store_browse_item(
        _fixture("storebrowse_281990.json"),
        currency="USD",
        bundle_memberships={21037: [121840]},
    )

    recurring = {
        item.package_id: item for item in parsed["edition_prices"] if item.price_type == "recurring"
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
        "included_items": {"included_packages": [{"best_purchase_option": {"packageid": 999}}]},
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


def test_package_price_rows_preserve_explicit_observation_states() -> None:
    parsed = parse_app_details(
        {
            "name": "Example",
            "type": "Game",
            "package_groups": [
                {
                    "subs": [
                        {"packageid": 10, "price_in_cents": 1000},
                        {"packageid": 11},
                        {
                            "packageid": 12,
                            "price_in_cents_with_discount": 777,
                            "percent_savings": 0,
                        },
                        {
                            "packageid": 320246,
                            "is_free_license": True,
                            "price_in_cents_with_discount": 0,
                            "percent_savings": 0,
                        },
                        {
                            "packageid": 13,
                            "is_free": True,
                            "price_in_cents": 1000,
                            "price_in_cents_with_discount": 0,
                            "percent_savings": 100,
                        },
                    ]
                }
            ],
        },
        normalize_locale("en-US"),
        store_country="KZ",
        store_browse={
            "response": {
                "store_items": [
                    {
                        "purchase_options": [
                            {
                                "packageid": 10,
                                "original_price_in_cents": 1000,
                                "final_price_in_cents": 800,
                                "discount_pct": 20,
                            }
                        ]
                    }
                ]
            }
        },
    )
    prices = {item.package_id: item for item in parsed["edition_prices"]}
    assert (prices[10].initial, prices[10].final) == (1000, 800)
    assert (prices[11].initial, prices[11].final, prices[11].price_region) == (None, None, "KZ")
    assert (prices[12].initial, prices[12].final, prices[12].discount_percent) == (777, 777, 0)
    assert (
        prices[320246].initial,
        prices[320246].final,
        prices[320246].discount_percent,
    ) == (0, 0, None)
    assert (prices[13].initial, prices[13].final, prices[13].discount_percent) == (1000, 0, 100)


def test_source_discount_token_runtime_restriction_and_workshop_semantics() -> None:
    price = parse_store_browse_item(
        {
            "purchase_options": [
                {
                    "packageid": 10,
                    "price_region": "US",
                    "original_price_in_cents": 1000,
                    "final_price_in_cents": 500,
                    "discount_pct": 50,
                    "active_discounts": [
                        {
                            "discount_description": "#unknown_discount_token",
                            "discount_end_date": 1_800_000_000,
                        }
                    ],
                }
            ]
        }
    )["edition_prices"][0]
    assert price.discount_type == "#unknown_discount_token"
    assert price.discount_end_at is not None
    diagnostics = annotate_package_price_observations(
        [price],
        {10: {"OnlyAllowRunInCountries": "CA,MX"}},
    )
    assert price.run_region_restricted is True
    assert price.regional_edition is None
    assert any(item["code"] == "steam_regional_edition_source_unavailable" for item in diagnostics)
    workshop = parse_workshop_stats(
        {"common": {"category": {"category_33": "1"}}},
        '\\"workshopNumbers\\":{\\"total\\":12}',
        collection_html='\\"workshopNumbers\\":{\\"total\\":3}',
    )
    assert (
        workshop.workshop_available,
        workshop.published_file_count,
        workshop.collection_count,
    ) == (
        True,
        12,
        None,
    )


def test_storebrowse_identity_tags_and_depot_source_keys_are_not_legacy_indexes() -> None:
    browse = parse_store_browse_item(
        {
            "tags": [{"tagid": 1755, "weight": 954}],
            "basic_info": {
                "developers": [{"name": "Studio", "creator_clan_account_id": 42}],
                "publishers": [{"name": "Publisher", "creator_clan_account_id": 43}],
            },
        },
        language="en",
    )
    assert [(item.tag_id, item.weight) for item in browse["tags"]] == [(1755, 954)]
    assert {
        (item.status, item.creator_clan_account_id, item.credited_name)
        for item in browse["organizations"]
    } == {("developer", 42, "Studio"), ("publisher", 43, "Publisher")}
    depots = parse_depots(
        {
            "depots": {
                "10": {
                    "optionaldlc": "99",
                    "systemdefined": "1",
                    "config": {"language": "brazilian", "oslist": "windows,linux"},
                    "manifests": {"public": {"download": 0, "size": 0}},
                }
            }
        }
    )
    depot = depots["depots"][0]
    assert (depot.language, depot.optional_dlc_app_id, depot.system_defined) == ("pt-BR", 99, True)
    assert depots["manifests"][0].download_size == 0


def test_branch_profiles_combine_common_os_language_and_shared_depots() -> None:
    branches = parse_build_branches(
        {
            "depots": {
                "branches": {"public": {"buildid": "7"}},
                "1": {"manifests": {"public": {"download": 100, "size": 100}}},
                "2": {
                    "config": {"oslist": "windows,linux"},
                    "manifests": {"public": {"download": 50, "size": 50}},
                },
                "3": {
                    "config": {"oslist": "windows"},
                    "manifests": {"public": {"download": 200, "size": 200}},
                },
                "4": {
                    "config": {"oslist": "linux"},
                    "manifests": {"public": {"download": 300, "size": 300}},
                },
                "5": {
                    "config": {"language": "russian"},
                    "manifests": {"public": {"download": 10, "size": 10}},
                },
                "6": {
                    "config": {"language": "english"},
                    "manifests": {"public": {"download": 20, "size": 20}},
                },
                "7": {"depotfromapp": "99", "config": {"oslist": "windows"}},
            }
        },
        shared_app_infos={
            99: {"depots": {"7": {"manifests": {"public": {"download": 500, "size": 500}}}}}
        },
    )
    public = next(branch for branch in branches if branch.name == "public")
    # Windows and Linux both include depot 2; Windows also includes the
    # resolved shared depot. Each profile includes one selected language.
    assert (
        public.download_size_min,
        public.download_size_median,
        public.download_size_max,
    ) == (460, 665, 870)
    assert (public.disk_size_min, public.disk_size_median, public.disk_size_max) == (460, 665, 870)


def test_numeric_age_vac_negative_and_descriptor_title_cleanup() -> None:
    parsed = parse_app_details(
        {
            "name": "Cyberpunk 2077",
            "type": "Game",
            "ratings": {
                "usk": {"rating": "6", "descriptors": "Cyberpunk 2077 contains Strong Language"}
            },
        },
        normalize_locale("en-US"),
        app_info={"common": {"category": {"category_2": "1"}}},
    )
    assert parsed["age_ratings"][0].minimum_age == 6
    assert parsed["vac_enabled"] is False
    assert "cyberpunk 2077 strong language" not in {
        item.name for item in parsed["descriptors"] if item.name
    }
