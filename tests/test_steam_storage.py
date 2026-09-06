from __future__ import annotations

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
from scraper.steam.parsers import parse_app_details, parse_build_branches
from scraper.steam.storage import persist_steam_scope, remove_steam_scope
from scraper.wikidata.config import ScraperConfig
from scraper.wikidata.orm import ScraperDatabase, SourceFact


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
                    {
                        "subs": [
                            {"packageid": 10, "currency": "USD", "price_in_cents": 1000}
                        ]
                    }
                ],
                "bundles": [
                    {
                        "bundleid": 20,
                        "name": "Bundle",
                        "item_ids": [10],
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
                        "name": "FIRST",
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
        )
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
            assert await session.get(SteamEditionPrice, (10, "KZ"))
            assert await session.get(SteamBundle, 20)
            assert await session.get(SteamBundleEdition, (20, 10))
            assert await session.get(SteamBundlePrice, (20, "KZ"))
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
