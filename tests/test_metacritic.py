import httpx
import pytest
import respx

from scraper.metacritic.client import _metacritic_urls, fetch_metacritic

METACRITIC_HTML = """
<span data-testid="global-score-value">84</span>
<div data-testid="global-score-header">Metascore</div>
<span data-testid="global-score-value">7.1</span>
<div data-testid="global-score-header">User Score</div>
"""


def test_metacritic_title_fallback_builds_pc_url() -> None:
    urls = _metacritic_urls("Horizon Zero Dawn™ Complete Edition", None)

    assert urls[0] == (
        "https://www.metacritic.com/game/pc/horizon-zero-dawn-complete-edition/"
    )


@pytest.mark.asyncio
@respx.mock
async def test_metacritic_fetches_title_fallback_when_steam_url_is_missing() -> None:
    route = respx.get(
        "https://www.metacritic.com/game/pc/horizon-zero-dawn-complete-edition/"
    ).mock(return_value=httpx.Response(200, text=METACRITIC_HTML))

    async with httpx.AsyncClient() as client:
        data, diagnostic = await fetch_metacritic(
            client,
            title="Horizon Zero Dawn™ Complete Edition",
        )

    assert route.called
    assert diagnostic is None
    assert data is not None
    assert data.url == route.calls[0].response.url
    assert data.critic_score == 84
    assert data.user_score == 7.1
