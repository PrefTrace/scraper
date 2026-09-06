import asyncio
import json
from collections.abc import Mapping
from typing import Any

import httpx

from .locales import LocaleInfo


class SteamClientError(RuntimeError):
    pass


class SteamClient:
    def __init__(self, client: httpx.AsyncClient) -> None:
        self.client = client

    async def _get(self, url: str, *, params: Mapping[str, Any] | None = None) -> httpx.Response:
        last_error: Exception | None = None
        for attempt in range(3):
            try:
                response = await self.client.get(url, params=params)
                if response.status_code == 429 or response.status_code >= 500:
                    if attempt < 2:
                        retry_after = response.headers.get("Retry-After")
                        delay = min(float(retry_after or (0.5 * (attempt + 1))), 3.0)
                        await asyncio.sleep(delay)
                        continue
                response.raise_for_status()
                return response
            except (httpx.RequestError, httpx.HTTPStatusError) as exc:
                last_error = exc
                if attempt < 2:
                    await asyncio.sleep(0.5 * (attempt + 1))
                    continue
                break
        raise SteamClientError(f"Steam request failed: {url}") from last_error

    @staticmethod
    def _localized_params(
        locale: LocaleInfo,
        store_country: str | None,
    ) -> dict[str, str]:
        params = {"l": locale.steam_language}
        if store_country is not None:
            params["cc"] = store_country
        return params

    async def app_details(
        self,
        app_id: int,
        locale: LocaleInfo,
        *,
        store_country: str | None = None,
    ) -> dict[str, Any]:
        response = await self._get(
            "https://store.steampowered.com/api/appdetails",
            params={
                "appids": app_id,
                **self._localized_params(locale, store_country),
            },
        )
        payload = response.json()
        if not isinstance(payload, dict):
            raise SteamClientError(f"Unexpected Steam appdetails response for app {app_id}")
        entry = payload.get(str(app_id), {})
        if not entry.get("success") or not isinstance(entry.get("data"), dict):
            raise SteamClientError(f"Steam app {app_id} was not found")
        data = entry["data"]
        if not isinstance(data, dict):
            raise SteamClientError(f"Unexpected Steam appdetails data for app {app_id}")
        return data

    async def store_page(
        self,
        app_id: int,
        locale: LocaleInfo,
        *,
        store_country: str | None = None,
    ) -> str:
        response = await self._get(
            f"https://store.steampowered.com/app/{app_id}/",
            params=self._localized_params(locale, store_country),
        )
        return response.text

    async def achievements_page(self, app_id: int, locale: LocaleInfo) -> str:
        response = await self._get(
            f"https://steamcommunity.com/stats/{app_id}/achievements/",
            params={"l": locale.steam_language},
        )
        return response.text

    async def achievement_schema(
        self,
        app_id: int,
        *,
        api_key: str,
        language: str,
    ) -> dict[str, Any]:
        response = await self._get(
            "https://api.steampowered.com/ISteamUserStats/GetSchemaForGame/v2/",
            params={"key": api_key, "appid": app_id, "l": language},
        )
        payload = response.json()
        if not isinstance(payload, dict):
            raise SteamClientError(f"Unexpected Steam achievement schema for app {app_id}")
        return payload

    async def global_achievement_percentages(
        self,
        app_id: int,
        *,
        api_key: str,
    ) -> dict[str, Any]:
        response = await self._get(
            "https://api.steampowered.com/ISteamUserStats/GetGlobalAchievementPercentagesForApp/v2/",
            params={"key": api_key, "gameid": app_id},
        )
        payload = response.json()
        if not isinstance(payload, dict):
            raise SteamClientError(
                f"Unexpected Steam global achievement percentages for app {app_id}"
            )
        return payload

    async def store_app_list_page(
        self,
        *,
        api_key: str,
        last_appid: int | None = None,
        max_results: int = 50_000,
        include_games: bool = True,
        include_dlc: bool = True,
        include_software: bool = True,
        include_videos: bool = False,
        include_hardware: bool = False,
    ) -> dict[str, Any]:
        params: dict[str, Any] = {
            "key": api_key,
            "max_results": max_results,
            "include_games": int(include_games),
            "include_dlc": int(include_dlc),
            "include_software": int(include_software),
            "include_videos": int(include_videos),
            "include_hardware": int(include_hardware),
        }
        if last_appid is not None:
            params["last_appid"] = last_appid
        response = await self._get(
            "https://api.steampowered.com/IStoreService/GetAppList/v1/",
            params=params,
        )
        payload = response.json()
        if not isinstance(payload, dict):
            raise SteamClientError("Unexpected Steam app list response")
        return payload

    async def review_page(
        self,
        app_id: int,
        *,
        language: str,
        review_type: str,
        cursor: str = "*",
    ) -> dict[str, Any]:
        response = await self._get(
            f"https://store.steampowered.com/appreviews/{app_id}",
            params={
                "json": 1,
                "filter": "all",
                "language": language,
                "day_range": 365,
                "cursor": cursor,
                "review_type": review_type,
                "purchase_type": "all",
                "num_per_page": 100,
            },
        )
        payload = response.json()
        if not isinstance(payload, dict):
            raise SteamClientError(f"Unexpected Steam reviews response for app {app_id}")
        if payload.get("success") != 1:
            raise SteamClientError(f"Steam reviews unavailable for app {app_id}")
        return payload

    async def public_app_info(self, app_id: int) -> dict[str, Any]:
        """Fetch public Steam AppInfo without a publisher API key.

        The service exposes the public AppInfo/PICS data used by SteamCMD,
        including public depot branches. Password-protected branch metadata is
        filtered by ``parse_build_branches`` before it reaches storage.
        """

        response = await self._get(
            f"https://api.steamcmd.net/v1/info/{app_id}",
        )
        payload = response.json()
        if not isinstance(payload, dict):
            raise SteamClientError(f"Unexpected Steam AppInfo response for app {app_id}")
        apps = payload.get("data")
        result = apps.get(str(app_id)) if isinstance(apps, dict) else None
        if not isinstance(result, dict):
            raise SteamClientError(f"Steam AppInfo has no app {app_id}")
        return result

    async def store_browse_items(
        self,
        app_id: int,
        locale: LocaleInfo,
        *,
        store_country: str | None = None,
        bundle_id: int | None = None,
    ) -> dict[str, Any]:
        """Fetch public structured StoreBrowse purchase/media data."""

        ids = [{"bundleid": bundle_id}] if bundle_id is not None else [{"appid": app_id}]
        input_json = {
            "ids": ids,
            "context": {
                "language": locale.steam_language,
                "country_code": store_country or "US",
                "steam_realm": 1,
            },
            "data_request": {
                "include_assets": True,
                "include_release": True,
                "include_platforms": True,
                "include_all_purchase_options": True,
                "include_screenshots": True,
                "include_trailers": True,
                "include_basic_info": True,
                "include_supported_languages": True,
                "include_included_items": True,
                "include_links": True,
            },
        }
        response = await self._get(
            "https://api.steampowered.com/IStoreBrowseService/GetItems/v1/",
            params={"input_json": json.dumps(input_json, separators=(",", ":"))},
        )
        payload = response.json()
        if not isinstance(payload, dict):
            raise SteamClientError(f"Unexpected Steam StoreBrowse response for app {app_id}")
        return payload
