from __future__ import annotations

import httpx
import respx
from sqlalchemy import select

from scraper.pcgamingwiki.client import (
    PCGAMINGWIKI_API_URL,
    PCGAMINGWIKI_REDIRECT_URL,
    _parse_infobox,
    _steam_app_ids,
)
from scraper.sources.pcgamingwiki import PCGamingWikiSyncService
from scraper.wikidata.config import ScraperConfig
from scraper.wikidata.orm import ScraperDatabase, SourceFact

WIKITEXT = """
{{Infobox game
| title = Portal 2
| steam appid = 620
| developers = [[Valve Corporation]]
| publishers = [[Valve Corporation]]
| engines = [[Source]]
| release dates = {{Release date|US|2011-04-19}}
| gogcom = portal_2
| hltb = portal-2
}}
== Availability ==
{{Availability|Windows}}
"""


def test_pcgamingwiki_infobox_parser_extracts_orm_ready_values() -> None:
    fields = _parse_infobox(WIKITEXT)

    assert _steam_app_ids(WIKITEXT) == [620]
    assert fields["developers"] == "Valve Corporation"
    assert fields["engines"] == "Source"
    assert "2011-04-19" in fields["release dates"]


@respx.mock
async def test_pcgamingwiki_search_fallback_and_ttl_cache(tmp_path) -> None:
    redirect = respx.get(PCGAMINGWIKI_REDIRECT_URL).mock(
        return_value=httpx.Response(403, text="forbidden")
    )
    def api_response(request: httpx.Request) -> httpx.Response:
        if request.url.params.get("action") == "opensearch":
            return httpx.Response(
                200,
                json=[
                    "Portal 2",
                    ["Portal 2"],
                    [""],
                    ["https://www.pcgamingwiki.com/wiki/Portal_2"],
                ],
            )
        return httpx.Response(
            200,
            json={"parse": {"pageid": 123, "wikitext": {"*": WIKITEXT}}},
        )

    api = respx.get(PCGAMINGWIKI_API_URL).mock(side_effect=api_response)
    config = ScraperConfig(
        database_url=f"sqlite+aiosqlite:///{(tmp_path / 'pcgw.sqlite3').as_posix()}",
        pcgamingwiki_min_interval_seconds=0,
    )
    database = ScraperDatabase(config)
    service = PCGamingWikiSyncService(database)
    try:
        first = await service.refresh(620, title="Portal 2")
        second = await service.refresh(620, title="Portal 2")

        assert first.status == second.status == "ready"
        assert redirect.call_count == 1
        assert api.call_count == 2
        async with database.session() as session:
            facts = (
                await session.scalars(
                    select(SourceFact).where(
                        SourceFact.source == "pcgamingwiki",
                        SourceFact.steam_app_id == 620,
                    )
                )
            ).all()
        assert any(
            fact.path == "developers[0]" and fact.value_text == "Valve Corporation"
            for fact in facts
        )
        assert any(
            fact.path == "external_ids.gogcom" and fact.value_text == "portal_2"
            for fact in facts
        )
    finally:
        await database.dispose()
