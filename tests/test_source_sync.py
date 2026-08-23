from __future__ import annotations

from scraper.models import HltbData
from scraper.sources.hltb import HltbSyncService
from scraper.sources.steam import SteamGameSyncService
from scraper.wikidata.config import ScraperConfig
from scraper.wikidata.orm import ScraperDatabase, SourceFact, SourceRefresh


def _database(tmp_path, name: str) -> ScraperDatabase:
    path = (tmp_path / name).as_posix()
    return ScraperDatabase(ScraperConfig(database_url=f"sqlite+aiosqlite:///{path}"))


async def _source_facts(database: ScraperDatabase, source: str, app_id: int) -> list[SourceFact]:
    from sqlalchemy import select

    async with database.session() as session:
        return (
            await session.scalars(
                select(SourceFact).where(
                    SourceFact.source == source,
                    SourceFact.steam_app_id == app_id,
                )
            )
        ).all()


async def test_hltb_source_refresh_uses_ttl_cache(monkeypatch, tmp_path) -> None:
    calls = 0

    async def fake_fetch(_title: str):
        nonlocal calls
        calls += 1
        return HltbData(id=7, name="Example", main_story_hours=4.5), None

    monkeypatch.setattr("scraper.sources.hltb._load_hltb", fake_fetch)
    database = _database(tmp_path, "hltb.sqlite3")
    service = HltbSyncService(database)
    try:
        first = await service.refresh(42, "Example")
        second = await service.refresh(42, "Example")

        assert first.status == "ready"
        assert second.status == "ready"
        assert calls == 1
        facts = await _source_facts(database, "hltb", 42)
        assert {(fact.path, fact.value_type, fact.value_float) for fact in facts} >= {
            ("main_story_hours", "float", 4.5)
        }
    finally:
        await database.dispose()


async def test_steam_details_and_substructures_are_orm_cached(monkeypatch, tmp_path) -> None:
    class FakeSteamClient:
        calls = 0

        def __init__(self, _http) -> None:
            pass

        async def app_details(self, _app_id, _locale, *, store_country=None):
            type(self).calls += 1
            return {
                "name": "Example",
                "type": "game",
                "short_description": "Short <b>description</b>",
                "release_date": {"date": "Apr 18, 2011", "coming_soon": False},
                "developers": ["Dev"],
                "publishers": ["Pub"],
                "platforms": {"windows": True},
                "supported_languages": "English",
            }

        async def store_page(self, _app_id, _locale, *, store_country=None):
            type(self).calls += 1
            return "<html></html>"

        async def achievements_page(self, _app_id, _locale):
            type(self).calls += 1
            return "<html></html>"

        async def review_page(self, _app_id, *, language, review_type, cursor="*"):
            type(self).calls += 1
            return {
                "success": 1,
                "query_summary": {
                    "review_score": 9,
                    "review_score_desc": "Very Positive",
                    "total_positive": 9,
                    "total_negative": 1,
                    "total_reviews": 10,
                },
                "reviews": [],
                "cursor": cursor,
            }

    monkeypatch.setattr("scraper.sources.steam.SteamClient", FakeSteamClient)
    database = _database(tmp_path, "steam.sqlite3")
    service = SteamGameSyncService(database)
    try:
        first = await service.refresh(42, languages=["en-US"], store_country="kz")
        calls_after_first = FakeSteamClient.calls
        second = await service.refresh(42, languages=["en-US"], store_country="kz")

        assert len(first.refreshes) == len(second.refreshes) == 7
        assert first.developers == second.developers == ["Dev"]
        assert first.publishers == second.publishers == ["Pub"]
        assert calls_after_first > 0
        assert FakeSteamClient.calls == calls_after_first
        async with database.session() as session:
            state = await session.get(
                SourceRefresh,
                {"source": "steam", "steam_app_id": 42, "scope": "details:en-US:kz"},
            )
            assert state is not None and state.status == "ready"
        facts = await _source_facts(database, "steam", 42)
        assert any(fact.path == "localized.name" and fact.value_text == "Example" for fact in facts)
    finally:
        await database.dispose()
