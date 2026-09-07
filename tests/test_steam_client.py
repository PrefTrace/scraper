import httpx
import pytest
import respx

from scraper.steam.client import SteamClient
from scraper.steam.locales import normalize_locale


def test_store_country_is_not_derived_from_requested_language() -> None:
    locale = normalize_locale("ru-RU")

    assert SteamClient._localized_params(locale, "kz") == {"cc": "kz", "l": "russian"}
    assert SteamClient._localized_params(locale, None) == {"l": "russian"}


@pytest.mark.asyncio
@respx.mock
async def test_public_app_info_requires_no_publisher_key() -> None:
    route = respx.get("https://api.steamcmd.net/v1/info/42").mock(
        return_value=httpx.Response(
            200,
            json={"data": {"42": {"depots": {"branches": {"public": {"buildid": "7"}}}}}},
        )
    )

    async with httpx.AsyncClient() as http:
        payload = await SteamClient(http).public_app_info(42)

    assert payload["depots"]["branches"]["public"]["buildid"] == "7"
    assert "key" not in route.calls[0].request.url.params


@pytest.mark.asyncio
@respx.mock
async def test_public_category_registry_never_uses_a_web_api_key() -> None:
    route = respx.get(
        "https://api.steampowered.com/IStoreBrowseService/GetStoreCategories/v1/"
    ).mock(
        return_value=httpx.Response(
            200,
            json={"response": {"categories": [{"categoryid": 2, "display_name": "Single-player"}]}},
        )
    )

    async with httpx.AsyncClient() as http:
        registry = await SteamClient(http).category_registry(locale=normalize_locale("en-US"))

    assert registry == {2: "Single-player"}
    assert "key" not in route.calls[0].request.url.params
