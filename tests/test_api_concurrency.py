import asyncio

import pytest

import scraper.api as api
from scraper.models import HltbData, MetacriticData

DETAILS = {
    "type": "game",
    "name": "Concurrent Test Game",
    "steam_appid": 620,
    "detailed_description": "Full description",
    "short_description": "Short description",
    "release_date": {"coming_soon": False, "date": "Apr 18, 2011"},
    "developers": [],
    "publishers": [],
    "platforms": {"windows": True},
    "categories": [],
    "genres": [],
    "supported_languages": "English",
    "screenshots": [],
    "movies": [],
    "ratings": {},
}


STORE_HTML = ""
ACHIEVEMENTS_HTML = ""


@pytest.mark.asyncio
async def test_independent_scrape_phases_overlap(monkeypatch: pytest.MonkeyPatch) -> None:
    store_started = asyncio.Event()
    achievements_started = asyncio.Event()
    review_started = asyncio.Event()
    hltb_started = asyncio.Event()
    overlap: dict[str, bool] = {}

    class FakeSteamClient:
        def __init__(self, _client: object) -> None:
            pass

        async def app_details(
            self,
            _app_id: int,
            _locale: object,
            *,
            store_country: str | None,
        ) -> dict[str, object]:
            assert store_country == "kz"
            return DETAILS

        async def store_page(
            self,
            _app_id: int,
            _locale: object,
            *,
            store_country: str | None,
        ) -> str:
            assert store_country == "kz"
            store_started.set()
            try:
                await asyncio.wait_for(achievements_started.wait(), timeout=0.5)
                overlap["store_and_achievements"] = True
            except TimeoutError:
                overlap["store_and_achievements"] = False
            return STORE_HTML

        async def achievements_page(self, _app_id: int, _locale: object) -> str:
            achievements_started.set()
            await store_started.wait()
            return ACHIEVEMENTS_HTML

        async def review_page(
            self,
            _app_id: int,
            *,
            language: str,
            review_type: str,
            cursor: str = "*",
        ) -> dict[str, object]:
            if not review_started.is_set():
                review_started.set()
                try:
                    await asyncio.wait_for(hltb_started.wait(), timeout=0.5)
                    overlap["reviews_and_hltb"] = True
                except TimeoutError:
                    overlap["reviews_and_hltb"] = False
            return {
                "success": 1,
                "query_summary": {
                    "review_score": 9,
                    "review_score_desc": "Overwhelmingly Positive",
                    "total_positive": 9,
                    "total_negative": 1,
                    "total_reviews": 10,
                },
                "cursor": cursor,
                "reviews": [],
            }

    async def fake_hltb(_title: str) -> tuple[HltbData, None]:
        hltb_started.set()
        return HltbData(name="Concurrent Test Game", main_story_hours=1), None

    async def fake_metacritic(
        _client: object,
        *,
        title: str,
        steam_url: str | None,
        critic_score: int | None,
    ) -> tuple[MetacriticData, None]:
        return MetacriticData(url=steam_url, critic_score=critic_score), None

    monkeypatch.setattr(api, "SteamClient", FakeSteamClient)
    monkeypatch.setattr(api, "fetch_hltb", fake_hltb)
    monkeypatch.setattr(api, "fetch_metacritic", fake_metacritic)

    await api.scrape(
        "https://store.steampowered.com/app/620/Concurrent_Test_Game/",
        languages=["en-US"],
        positive_review_count=0,
        negative_review_count=0,
        review_pages=1,
    )

    assert overlap == {
        "store_and_achievements": True,
        "reviews_and_hltb": True,
    }
