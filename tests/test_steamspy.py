from __future__ import annotations

import httpx
import respx
from sqlalchemy import select

from scraper.sources.steamspy_deprecated import SteamSpySyncService
from scraper.steamspy_deprecated.client import STEAMSPY_API_URL, parse_stats
from scraper.wikidata_deprecated.config import ScraperConfig
from scraper.wikidata_deprecated.orm import ScraperDatabase, SourceFact

PAYLOAD = {
    "appid": 620,
    "owners": "5,000,000 .. 10,000,000",
    "average_forever": 840,
    "average_2weeks": 120,
    "median_forever": 600,
    "median_2weeks": 90,
    "ccu": 1959,
    "tags": {"Platformer": 7477, "Puzzle": 7394},
}


def test_steamspy_parser_keeps_only_requested_metrics() -> None:
    stats = parse_stats(PAYLOAD, app_id=620)

    assert stats.app_id == 620
    assert stats.owners_min == 5_000_000
    assert stats.owners_max == 10_000_000
    assert stats.average_forever_minutes == 840
    assert stats.average_two_weeks_minutes == 120
    assert stats.median_forever_minutes == 600
    assert stats.median_two_weeks_minutes == 90
    assert stats.ccu == 1959
    assert stats.tags == {"Platformer": 7477, "Puzzle": 7394}


@respx.mock
async def test_steamspy_stats_are_saved_as_orm_facts_and_ttl_cached(tmp_path) -> None:
    route = respx.get(STEAMSPY_API_URL).mock(return_value=httpx.Response(200, json=PAYLOAD))
    config = ScraperConfig(
        database_url=f"sqlite+aiosqlite:///{(tmp_path / 'steamspy.sqlite3').as_posix()}",
        steamspy_min_interval_seconds=0,
    )
    database = ScraperDatabase(config)
    service = SteamSpySyncService(database)
    try:
        first = await service.refresh(620)
        second = await service.refresh(620)

        assert first.status == second.status == "ready"
        assert route.call_count == 1
        async with database.session() as session:
            facts = (
                await session.scalars(
                    select(SourceFact).where(
                        SourceFact.source == "steamspy",
                        SourceFact.steam_app_id == 620,
                        SourceFact.scope == "stats",
                    )
                )
            ).all()
        fact_values = {(fact.path, fact.value_type, fact.value_int) for fact in facts}
        assert ("app_id", "int", 620) in fact_values
        assert ("owners_min", "int", 5_000_000) in fact_values
        assert ("owners_max", "int", 10_000_000) in fact_values
        assert ("average_forever_minutes", "int", 840) in fact_values
        assert ("median_two_weeks_minutes", "int", 90) in fact_values
        assert ("ccu", "int", 1959) in fact_values
        assert ("tags.Platformer", "int", 7477) in fact_values
    finally:
        await database.dispose()
