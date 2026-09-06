from __future__ import annotations

import asyncio
from datetime import timedelta

import pytest
from sqlalchemy import select
from sqlalchemy.orm import selectinload

from scraper.wikidata_deprecated.config import ScraperConfig
from scraper.wikidata_deprecated.orm import (
    ScraperDatabase,
    WikidataEntity,
    WikidataFact,
    WikidataGame,
    WikidataGameLink,
)
from scraper.wikidata_deprecated.sync import WikidataSyncService, _save_entity_payload, utcnow


def _item(qid: str) -> dict[str, object]:
    return {"entity-type": "item", "numeric-id": int(qid[1:]), "id": qid}


def _payload() -> dict[str, object]:
    return {
        "id": "Q100",
        "lastrevid": 123,
        "labels": {"en": {"language": "en", "value": "Example game"}},
        "descriptions": {"en": {"language": "en", "value": "A game"}},
        "aliases": {"en": [{"language": "en", "value": "Demo"}]},
        "claims": {
            "P31": [
                {
                    "rank": "normal",
                    "mainsnak": {
                        "snaktype": "value",
                        "datavalue": {
                            "type": "wikibase-entityid",
                            "value": _item("Q7889"),
                        },
                    },
                    "qualifiers": {
                        "P580": [
                            {
                                "snaktype": "value",
                                "datavalue": {
                                    "type": "time",
                                    "value": {
                                        "time": "+2020-01-01T00:00:00Z",
                                        "precision": 11,
                                        "calendarmodel": "http://www.wikidata.org/entity/Q1985727",
                                    },
                                },
                            }
                        ]
                    },
                }
            ]
        },
    }


@pytest.mark.asyncio
async def test_orm_persists_entity_fact_and_qualifier(tmp_path) -> None:
    database = ScraperDatabase(
        ScraperConfig(database_url=f"sqlite+aiosqlite:///{(tmp_path / 'data.sqlite3').as_posix()}")
    )
    try:
        await database.create_schema()
        observed_at = utcnow()
        async with database.session() as session:
            await _save_entity_payload(
                session,
                _payload(),
                full=True,
                observed_at=observed_at,
            )
            await session.commit()

        async with database.session() as session:
            entity = await session.get(WikidataEntity, "Q100")
            facts = (
                await session.scalars(
                    select(WikidataFact).options(selectinload(WikidataFact.qualifiers))
                )
            ).all()
            assert entity is not None
            assert entity.label == "Example game"
            assert entity.full_fetched_at == observed_at
            assert len(facts) == 1
            assert facts[0].value_qid == "Q7889"
            assert len(facts[0].qualifiers) == 1
            assert facts[0].qualifiers[0].value_time == "2020-01-01"
    finally:
        await database.dispose()


@pytest.mark.asyncio
async def test_fresh_entity_is_not_requested_again(tmp_path) -> None:
    config = ScraperConfig(
        database_url=f"sqlite+aiosqlite:///{(tmp_path / 'cache.sqlite3').as_posix()}",
        ttl_seconds=3600,
    )
    database = ScraperDatabase(config)
    service = WikidataSyncService(database)
    await service.ensure_schema()
    try:
        async with database.session() as session:
            await _save_entity_payload(
                session,
                _payload(),
                full=True,
                observed_at=utcnow(),
            )
            await session.commit()

        class FakeClient:
            calls = 0

            async def get_entities(self, ids, *, full):
                del ids, full
                self.calls += 1
                return {}

        client = FakeClient()
        await service._ensure_entities(
            client,
            {"Q100"},
            full_ids={"Q100"},
            force=False,
        )
        assert client.calls == 0

        async with database.session() as session:
            entity = await session.get(WikidataEntity, "Q100")
            assert entity is not None
            entity.full_fetched_at = utcnow() - timedelta(hours=2)
            await session.commit()

        await service._ensure_entities(
            client,
            {"Q100"},
            full_ids={"Q100"},
            force=False,
        )
        assert client.calls == 1
    finally:
        await database.dispose()


@pytest.mark.asyncio
async def test_empty_labels_are_cached_as_a_successful_fetch(tmp_path) -> None:
    database = ScraperDatabase(
        ScraperConfig(database_url=f"sqlite+aiosqlite:///{(tmp_path / 'empty.sqlite3').as_posix()}")
    )
    await database.create_schema()
    service = WikidataSyncService(database)
    try:
        async with database.session() as session:
            await _save_entity_payload(
                session,
                {"id": "Q101", "labels": {}, "descriptions": {}, "aliases": {}},
                full=False,
                observed_at=utcnow(),
            )
            await session.commit()

        class FakeClient:
            calls = 0

            async def get_entities(self, ids, *, full):
                del ids, full
                self.calls += 1
                return {}

        client = FakeClient()
        await service._ensure_entities(
            client,
            {"Q101"},
            full_ids=set(),
            force=False,
        )
        assert client.calls == 0
    finally:
        await database.dispose()


@pytest.mark.asyncio
async def test_concurrent_refreshes_share_entity_cache_lock(tmp_path) -> None:
    database = ScraperDatabase(
        ScraperConfig(
            database_url=f"sqlite+aiosqlite:///{(tmp_path / 'race.sqlite3').as_posix()}",
            ttl_seconds=3600,
        )
    )
    service = WikidataSyncService(database)
    await service.ensure_schema()
    try:

        class FakeClient:
            calls = 0

            async def get_entities(self, ids, *, full):
                del full
                self.calls += 1
                await asyncio.sleep(0.01)
                return {qid: _payload() for qid in ids}

        client = FakeClient()
        await asyncio.gather(
            service._ensure_entities(
                client,
                {"Q100"},
                full_ids={"Q100"},
                force=False,
            ),
            service._ensure_entities(
                client,
                {"Q100"},
                full_ids={"Q100"},
                force=False,
            ),
        )
        assert client.calls == 1
    finally:
        await database.dispose()


@pytest.mark.asyncio
async def test_missing_game_still_resolves_steam_organizations(monkeypatch, tmp_path) -> None:
    database = ScraperDatabase(
        ScraperConfig(
            database_url=f"sqlite+aiosqlite:///{(tmp_path / 'missing-game.sqlite3').as_posix()}",
            ttl_seconds=3600,
        )
    )
    service = WikidataSyncService(database)
    await service.ensure_schema()
    try:

        class FakeWikidataClient:
            search_calls = 0

            def __init__(self, _http, *, user_agent) -> None:
                del user_agent

            async def find_by_steam_app_id(self, _app_id):
                return []

            async def search_entities(self, name, *, limit):
                del limit
                type(self).search_calls += 1
                return {"Dev": ["Q200"], "Pub": ["Q201"]}[name]

            async def get_entities(self, ids, *, full):
                del full
                return {
                    qid: {
                        "id": qid,
                        "labels": {"en": {"language": "en", "value": qid}},
                        "descriptions": {},
                        "aliases": {},
                        "claims": {},
                    }
                    for qid in ids
                }

        monkeypatch.setattr("scraper.wikidata_deprecated.sync.WikidataClient", FakeWikidataClient)
        first = await service.refresh_game_task(42, client=object())
        dev = await service.refresh_organization_name("Dev", client=object())
        pub = await service.refresh_organization_name("Pub", client=object())
        await service.link_game_entity(42, dev.qids[0], relation="developer")
        await service.link_game_entity(42, pub.qids[0], relation="publisher")
        await service.refresh_entity(dev.qids[0], client=object())
        await service.refresh_entity(pub.qids[0], client=object())
        second = await service.refresh_game_task(42, client=object())

        assert first.status == "not_found"
        assert second.status == "not_found"
        assert first.item_qid is None
        assert FakeWikidataClient.search_calls == 2
        async with database.session() as session:
            links = (
                await session.scalars(
                    select(WikidataGameLink).where(WikidataGameLink.steam_app_id == 42)
                )
            ).all()
            game = await session.get(WikidataGame, 42)
            assert game is not None and game.item_qid is None
            assert {(link.relation, link.qid) for link in links} == {
                ("developer", "Q200"),
                ("publisher", "Q201"),
            }
    finally:
        await database.dispose()


def test_wikidata_config_reads_environment(monkeypatch) -> None:
    monkeypatch.setenv("SCRAPER_TTL_SECONDS", "60")
    monkeypatch.setenv("SCRAPER_CONCURRENCY", "4")
    monkeypatch.setenv("SCRAPER_BATCH_SIZE", "10")
    config = ScraperConfig.from_env()

    assert config.ttl_seconds == 60
    assert config.concurrency == 4
    assert config.batch_size == 10
