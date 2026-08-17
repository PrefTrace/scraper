import httpx
import pytest
import respx

from scraper.steam.apps import fetch_app_ids
from scraper.steam.client import SteamClient


@pytest.mark.asyncio
@respx.mock
async def test_official_app_id_source_continues_by_last_appid() -> None:
    def response(request: httpx.Request) -> httpx.Response:
        last_appid = request.url.params.get("last_appid")
        if last_appid is None:
            payload = {
                "response": {
                    "apps": [{"appid": 10}, {"appid": 20}],
                    "last_appid": 20,
                    "have_more_results": True,
                }
            }
        else:
            payload = {
                "response": {
                    "apps": [{"appid": 20}, {"appid": 30}],
                    "last_appid": 30,
                    "have_more_results": False,
                }
            }
        return httpx.Response(200, json=payload, request=request)

    route = respx.get(
        "https://api.steampowered.com/IStoreService/GetAppList/v1/"
    ).mock(side_effect=response)

    async with httpx.AsyncClient() as http:
        app_ids = await fetch_app_ids(SteamClient(http), api_key="test-key")

    assert app_ids == [10, 20, 30]
    assert route.calls[0].request.url.params["key"] == "test-key"
    assert route.calls[0].request.url.params["max_results"] == "50000"
    assert route.calls[0].request.url.params["include_games"] == "1"
    assert route.calls[1].request.url.params["last_appid"] == "20"


@pytest.mark.asyncio
@respx.mock
async def test_official_app_id_source_can_stop_at_requested_limit() -> None:
    route = respx.get(
        "https://api.steampowered.com/IStoreService/GetAppList/v1/"
    ).mock(
        return_value=httpx.Response(
            200,
            json={
                "response": {
                    "apps": [{"appid": 30}, {"appid": 10}, {"appid": 20}],
                    "last_appid": 30,
                    "have_more_results": True,
                }
            },
        )
    )

    async with httpx.AsyncClient() as http:
        app_ids = await fetch_app_ids(
            SteamClient(http),
            api_key="test-key",
            max_app_ids=2,
        )

    assert app_ids == [10, 20]
    assert len(route.calls) == 1
