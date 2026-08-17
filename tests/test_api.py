from collections.abc import Mapping

import httpx
import pytest
import respx

import scraper.api as api
from scraper.models import HltbData, MetacriticData

DETAILS = {
    "type": "game",
    "name": "Portal 2",
    "steam_appid": 620,
    "detailed_description": "Full <b>description</b>",
    "short_description": "Short description",
    "header_image": "https://cdn.example.test/header.jpg",
    "pc_requirements": {"minimum": "Windows 10"},
    "developers": ["Valve"],
    "publishers": ["Valve"],
    "platforms": {"windows": True, "mac": True, "linux": True},
    "categories": [{"id": 2, "description": "Single-player"}],
    "genres": [{"id": "1", "description": "Action"}],
    "supported_languages": "English<strong>*</strong>, Russian",
    "release_date": {"coming_soon": False, "date": "Apr 18, 2011"},
    "screenshots": [],
    "movies": [],
    "achievements": {"total": 1},
    "ratings": {},
    "metacritic": {"score": 95, "url": "https://www.metacritic.com/game/portal-2/"},
}


STORE_HTML = """
<div class="glance_tags popular_tags"><a class="app_tag">Puzzle</a></div>
<table class="game_language_options">
 <tr><th></th><th>Interface</th><th>Full Audio</th><th>Subtitles</th></tr>
 <tr><td>English</td><td><span>✓</span></td><td><span>✓</span></td><td><span>✓</span></td></tr>
</table>
"""

ACHIEVEMENTS_HTML = """
<div class="achieveRow"><div class="achieveImgHolder"><img src="https://cdn.example.test/a.jpg"></div>
<div class="achievePercent">50.0%</div>
<div class="achieveTxt"><h3>First</h3><h5>Start</h5></div></div>
"""


def _review_payload(request: httpx.Request) -> Mapping[str, object]:
    params = request.url.params
    review_type = params.get("review_type")
    language = params.get("language")
    if review_type == "all":
        return {
            "success": 1,
            "query_summary": {
                "review_score": 9,
                "review_score_desc": "Overwhelmingly Positive",
                "total_positive": 90,
                "total_negative": 10,
                "total_reviews": 100,
            },
            "cursor": "next",
            "reviews": [],
        }
    positive = review_type == "positive"
    return {
        "success": 1,
        "cursor": "next",
        "reviews": [
            {
                "recommendationid": f"{language}-{review_type}",
                "review": (
                    "This is a useful long review"
                    if positive
                    else "This is a useful negative review"
                ),
                "voted_up": positive,
                "language": "english",
                "votes_up": 4,
                "weighted_vote_score": "0.7",
            }
        ],
    }


@pytest.mark.asyncio
@respx.mock
async def test_scrape_returns_normalized_partial_result(monkeypatch: pytest.MonkeyPatch) -> None:
    respx.get("https://store.steampowered.com/api/appdetails").mock(
        side_effect=lambda request: httpx.Response(
            200,
            json={"620": {"success": True, "data": DETAILS}},
            request=request,
        )
    )
    respx.get("https://store.steampowered.com/app/620/").mock(
        return_value=httpx.Response(200, text=STORE_HTML)
    )
    respx.get("https://steamcommunity.com/stats/620/achievements/").mock(
        return_value=httpx.Response(200, text=ACHIEVEMENTS_HTML)
    )
    respx.get("https://store.steampowered.com/appreviews/620").mock(
        side_effect=lambda request: httpx.Response(
            200,
            json=_review_payload(request),
            request=request,
        )
    )

    async def fake_hltb(name: str):
        return HltbData(name=name, main_story_hours=10), None

    async def fake_metacritic(
        client,
        *,
        title: str,
        steam_url: str | None,
        critic_score: int | None,
    ):
        return MetacriticData(url=steam_url, critic_score=critic_score, user_score=8.5), None

    monkeypatch.setattr(api, "fetch_hltb", fake_hltb)
    monkeypatch.setattr(api, "fetch_metacritic", fake_metacritic)

    result = await api.scrape(
        "https://store.steampowered.com/app/620/Portal_2/",
        languages=["ru-RU", "en-US"],
        positive_review_count=1,
        negative_review_count=1,
        review_pages=1,
    )

    assert result.app_id == 620
    assert result.store_country == "kz"
    assert set(result.localizations) == {"ru-RU", "en-US"}
    assert {item.store_country for item in result.localizations.values()} == {"kz"}
    assert result.tags[0].name == "Puzzle"
    assert result.achievements[0].global_percent == 50.0
    assert result.reviews.positive[0].positive is True
    assert result.reviews.negative[0].positive is False
    assert result.metacritic is not None
    assert result.metacritic.user_score == 8.5

    detail_requests = [
        call.request
        for call in respx.calls
        if "store.steampowered.com/api/appdetails" in str(call.request.url)
    ]
    assert {(request.url.params["cc"], request.url.params["l"]) for request in detail_requests} == {
        ("kz", "russian"),
        ("kz", "english"),
    }

    store_requests = [
        call.request
        for call in respx.calls
        if "store.steampowered.com/app/620/" in str(call.request.url)
    ]
    assert store_requests[0].url.params["cc"] == "kz"
    assert store_requests[0].url.params["l"] == "russian"

    localized_summary_requests = {
        call.request.url.params["language"]
        for call in respx.calls
        if "store.steampowered.com/appreviews/620" in str(call.request.url)
        and call.request.url.params["review_type"] == "all"
    }
    assert localized_summary_requests == {"all", "russian", "english"}
