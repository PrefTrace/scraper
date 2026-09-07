from __future__ import annotations

import json
from pathlib import Path

import pytest
from sqlalchemy import select

from scraper.models import RatingSummary
from scraper.steam.locales import normalize_locale
from scraper.steam.orm import (
    SteamAchievement,
    SteamAchievementLocalization,
    SteamAgeRating,
    SteamApp,
    SteamAppEdition,
    SteamAppLocalization,
    SteamBuildBranch,
    SteamBundle,
    SteamBundleEdition,
    SteamBundlePrice,
    SteamDescriptor,
    SteamEdition,
    SteamEditionPrice,
    SteamExternalLink,
    SteamFeature,
    SteamMedia,
    SteamOrganizationCredit,
    SteamReview,
    SteamReviewLanguageStat,
    SteamSupportedLanguage,
    SteamSystemRequirement,
)
from scraper.steam.parsers import (
    parse_achievement_schema,
    parse_app_details,
    parse_build_branches,
    parse_global_achievement_percentages,
)
from scraper.steam.storage import persist_steam_scope, remove_all_steam_data, remove_steam_scope
from scraper.wikidata_deprecated.config import ScraperConfig
from scraper.wikidata_deprecated.orm import ScraperDatabase, SourceFact


@pytest.mark.asyncio
async def test_steam_details_are_persisted_in_tz_tables(tmp_path) -> None:
    database = ScraperDatabase(
        ScraperConfig(database_url=f"sqlite+aiosqlite:///{(tmp_path / 'steam.sqlite3').as_posix()}")
    )
    try:
        await database.create_schema()
        parsed = parse_app_details(
            {
                "name": "Example",
                "type": "game",
                "steam_appid": 42,
                "platforms": {"windows": True, "mac": False, "linux": True},
                "vac_enabled": True,
                "website": "https://example.com",
                "release_date": {"date": "Q3 2026", "coming_soon": True},
                "developers": ["Dev"],
                "publishers": ["Pub"],
                "short_description": "Short",
                "detailed_description": "Long",
                "pc_requirements": {"minimum": "Windows"},
                "categories": [{"id": 2, "description": "Single-player"}],
                "ratings": {"esrb": {"rating": "M", "rating_generated": True}},
                "content_descriptors": {"ids": [2]},
                "package_groups": [
                    {"subs": [{"packageid": 10, "currency": "USD", "price_in_cents": 1000}]}
                ],
                "bundles": [
                    {
                        "bundleid": 20,
                        "name": "Bundle",
                        "currency": "USD",
                        "price": 900,
                    }
                ],
                "screenshots": [
                    {
                        "id": 1,
                        "path_full": "https://example.com/shot.jpg",
                    }
                ],
                "achievements": [
                    {
                        "name": "First",
                        "apiname": "FIRST",
                        "displayName": "First",
                        "description": "Start",
                        "percent": 50,
                    }
                ],
                "support_info": {"url": "https://example.com/support", "email": "a@example.com"},
            },
            normalize_locale("en-US"),
            app_id=42,
            store_country="kz",
            store_browse={
                "purchase_options": [
                    {
                        "bundleid": 20,
                        "purchase_option_name": "Bundle",
                        "final_price_in_cents": 900,
                    }
                ],
                "_bundle_memberships": {20: [10, 11]},
            },
        )
        async with database.session() as session:
            session.add(SteamEdition(package_id=11))
            session.add(SteamEditionPrice(package_id=11, price_region="KZ"))
            await session.commit()

        async with database.session() as session:
            await persist_steam_scope(session, 42, "details:en-US:kz", parsed)
            await persist_steam_scope(
                session,
                42,
                "achievements:en-US",
                {"achievements": parsed["achievements"]},
            )
            await persist_steam_scope(
                session,
                42,
                "branches",
                {
                    "branches": parse_build_branches(
                        {"depots": {"branches": {"public": {"buildid": "7"}}}}
                    )
                },
            )
            await persist_steam_scope(
                session,
                42,
                "rating:ru:kz",
                RatingSummary(
                    review_language="ru",
                    total_reviews=3,
                    total_positive=2,
                    total_negative=1,
                    score=7,
                ),
            )
            await persist_steam_scope(
                session,
                42,
                "reviews:positive:4:all",
                [],
            )
            await session.commit()

        async with database.session() as session:
            assert await session.get(SteamApp, 42)
            assert await session.get(SteamAppLocalization, (42, "en-US"))
            assert (await session.scalars(select(SteamMedia))).all()
            assert await session.get(SteamEdition, 10)
            assert await session.get(SteamAppEdition, (42, 10))
            edition_price = await session.get(SteamEditionPrice, (10, "KZ"))
            assert edition_price and edition_price.currency == "USD"
            assert await session.get(SteamEditionPrice, (11, "KZ")) is None
            assert await session.get(SteamBundle, 20)
            assert await session.get(SteamBundleEdition, (20, 10))
            assert await session.get(SteamBundleEdition, (20, 11))
            bundle_price = await session.get(SteamBundlePrice, (20, "KZ"))
            assert bundle_price and bundle_price.currency == "USD"
            assert (await session.scalars(select(SteamFeature))).all()
            assert (await session.scalars(select(SteamAgeRating))).all()
            assert (await session.scalars(select(SteamDescriptor))).all()
            assert (await session.scalars(select(SteamSystemRequirement))).all()
            assert (await session.scalars(select(SteamOrganizationCredit))).all()
            assert (await session.scalars(select(SteamExternalLink))).all()
            assert (await session.scalars(select(SteamAchievement))).all()
            assert (await session.scalars(select(SteamAchievementLocalization))).all()
            assert (await session.scalars(select(SteamBuildBranch))).all()
            assert (await session.scalars(select(SteamReviewLanguageStat))).all()
            assert (await session.scalars(select(SteamReview))).all() == []
            assert (await session.scalars(select(SteamSupportedLanguage))).all() == []
            assert (await session.scalars(select(SourceFact))).all() == []

        async with database.session() as session:
            await remove_steam_scope(session, 42, "details:en-US:kz")
            await session.commit()
            assert await session.get(SteamApp, 42)
            assert await session.get(SteamEdition, 10)
    finally:
        await database.dispose()


@pytest.mark.asyncio
async def test_removing_an_app_preserves_shared_package_and_bundle_relations(tmp_path) -> None:
    database = ScraperDatabase(
        ScraperConfig(
            database_url=f"sqlite+aiosqlite:///{(tmp_path / 'shared-package.sqlite3').as_posix()}"
        )
    )
    try:
        await database.create_schema()
        app_one = parse_app_details(
            {
                "name": "First game",
                "type": "Game",
                "package_groups": [{"subs": [{"packageid": 10, "price_in_cents": 1000}]}],
            },
            normalize_locale("en-US"),
            app_id=1,
            store_country="KZ",
            store_browse={
                "purchase_options": [
                    {
                        "bundleid": 100,
                        "original_price_in_cents": 1000,
                        "final_price_in_cents": 1000,
                    }
                ],
                "_bundle_memberships": {100: [10]},
            },
        )
        app_two = parse_app_details(
            {
                "name": "Second game",
                "type": "Game",
                "package_groups": [{"subs": [{"packageid": 10, "price_in_cents": 1000}]}],
            },
            normalize_locale("en-US"),
            app_id=2,
            store_country="KZ",
        )
        async with database.session() as session:
            await persist_steam_scope(session, 1, "details:en-US:kz", app_one)
            await persist_steam_scope(session, 2, "details:en-US:kz", app_two)
            await session.commit()
        async with database.session() as session:
            await remove_all_steam_data(session, 1)
            await session.commit()
        async with database.session() as session:
            assert await session.get(SteamApp, 1) is None
            assert await session.get(SteamAppEdition, (2, 10)) is not None
            assert await session.get(SteamEdition, 10) is not None
            assert await session.get(SteamBundleEdition, (100, 10)) is not None
    finally:
        await database.dispose()


@pytest.mark.asyncio
async def test_unsupported_steam_types_are_not_indexed(tmp_path) -> None:
    database = ScraperDatabase(
        ScraperConfig(
            database_url=f"sqlite+aiosqlite:///{(tmp_path / 'unsupported.sqlite3').as_posix()}"
        )
    )
    try:
        await database.create_schema()
        parsed = parse_app_details(
            {"name": "Demo", "type": "demo", "release_date": {"coming_soon": False}},
            normalize_locale("en-US"),
            app_id=530620,
        )
        assert parsed["type"] is None
        async with database.session() as session:
            await persist_steam_scope(session, 530620, "details:en-US:kz", parsed)
            await session.commit()
            assert await session.get(SteamApp, 530620) is None
    finally:
        await database.dispose()


@pytest.mark.asyncio
async def test_supported_steam_types_persist_end_to_end(tmp_path) -> None:
    database = ScraperDatabase(
        ScraperConfig(database_url=f"sqlite+aiosqlite:///{(tmp_path / 'types.sqlite3').as_posix()}")
    )
    try:
        await database.create_schema()
        raw_types = {101: "Game", 102: "Software", 103: "DLC", 104: "Music"}
        async with database.session() as session:
            for app_id, raw_type in raw_types.items():
                parsed = parse_app_details(
                    {"name": raw_type, "type": raw_type},
                    normalize_locale("en-US"),
                    app_id=app_id,
                )
                await persist_steam_scope(session, app_id, "details:en-US:kz", parsed)
            await session.commit()
            apps = (await session.scalars(select(SteamApp))).all()
        assert {app.type for app in apps} == {"game", "application", "dlc", "soundtrack"}
    finally:
        await database.dispose()


@pytest.mark.asyncio
async def test_structured_achievement_snapshots_persist_one_base_row_and_two_locales(
    tmp_path,
) -> None:
    database = ScraperDatabase(
        ScraperConfig(
            database_url=f"sqlite+aiosqlite:///{(tmp_path / 'achievements.sqlite3').as_posix()}"
        )
    )
    fixture_dir = Path(__file__).parent / "fixtures" / "steam"
    try:
        await database.create_schema()
        percentages = parse_global_achievement_percentages(
            json.loads((fixture_dir / "achievement_percentages.json").read_text(encoding="utf-8"))
        )
        async with database.session() as session:
            for locale, filename in (
                ("en-US", "achievement_schema_en.json"),
                ("ru-RU", "achievement_schema_ru.json"),
            ):
                achievements = parse_achievement_schema(
                    json.loads((fixture_dir / filename).read_text(encoding="utf-8")),
                    language=locale,
                    percentages=percentages,
                )
                await persist_steam_scope(
                    session,
                    9001,
                    f"achievements:{locale}",
                    {"achievements": achievements},
                )
            await session.commit()
            base_rows = (await session.scalars(select(SteamAchievement))).all()
            localizations = (await session.scalars(select(SteamAchievementLocalization))).all()
        assert len(base_rows) == 2
        assert {row.achievement_id for row in base_rows} == {"ACH_FIRST_STEP", "ACH_SECRET"}
        assert len(localizations) == 4
        assert {row.language for row in localizations} == {"en-US", "ru-RU"}
    finally:
        await database.dispose()
