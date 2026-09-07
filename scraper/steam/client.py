import asyncio
import json
import os
from collections.abc import Mapping, Sequence
from typing import Any

import httpx

from .locales import LocaleInfo


class SteamClientError(RuntimeError):
    pass


_CATEGORY_REGISTRIES: dict[str, dict[int, str]] = {}
_CATEGORY_REGISTRY_LOCK = asyncio.Lock()


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

    async def workshop_page(self, app_id: int, *, section: str = "readytouseitems") -> str:
        """Fetch the public anonymous Workshop browse page."""

        response = await self._get(
            "https://steamcommunity.com/workshop/browse/",
            params={
                "appid": app_id,
                "browsesort": "trend",
                "section": section,
            },
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
    ) -> dict[str, Any]:
        response = await self._get(
            "https://api.steampowered.com/ISteamUserStats/GetGlobalAchievementPercentagesForApp/v2/",
            params={"gameid": app_id},
        )
        payload = response.json()
        if not isinstance(payload, dict):
            raise SteamClientError(
                f"Unexpected Steam global achievement percentages for app {app_id}"
            )
        return payload

    async def category_registry(
        self,
        *,
        locale: LocaleInfo | None = None,
    ) -> dict[int, str]:
        """Load a localized Steam category registry keyed by category ID."""

        language = (locale.steam_language if locale is not None else "english").casefold()
        if language in _CATEGORY_REGISTRIES:
            return dict(_CATEGORY_REGISTRIES[language])
        async with _CATEGORY_REGISTRY_LOCK:
            if language in _CATEGORY_REGISTRIES:
                return dict(_CATEGORY_REGISTRIES[language])
            response = await self._get(
                "https://api.steampowered.com/IStoreBrowseService/GetStoreCategories/v1/",
                params={"language": language},
            )
            payload = response.json()
            response_data = payload.get("response") if isinstance(payload, dict) else None
            raw_categories = (
                response_data.get("categories")
                if isinstance(response_data, dict)
                else payload.get("categories")
                if isinstance(payload, dict)
                else None
            )
            registry: dict[int, str] = {}
            for raw in raw_categories if isinstance(raw_categories, list) else []:
                if not isinstance(raw, dict):
                    continue
                raw_category_id = raw.get("categoryid", raw.get("category_id", raw.get("id")))
                name = raw.get("display_name", raw.get("name", raw.get("description")))
                if raw_category_id is None:
                    continue
                try:
                    category_id = int(raw_category_id)
                except (TypeError, ValueError):
                    continue
                if isinstance(name, str) and name.strip():
                    registry[category_id] = name.strip()
            _CATEGORY_REGISTRIES[language] = registry
            return dict(registry)

    async def localized_tag_names(
        self,
        tag_ids: Sequence[int],
        locale: LocaleInfo,
    ) -> dict[int, str]:
        """Resolve encountered tag IDs through Steam's structured endpoint."""

        normalized = list(dict.fromkeys(int(tag_id) for tag_id in tag_ids))
        result: dict[int, str] = {}
        for offset in range(0, len(normalized), 100):
            chunk = normalized[offset : offset + 100]
            response = await self._get(
                "https://api.steampowered.com/IStoreService/GetLocalizedNameForTags/v1/",
                params={
                    "input_json": json.dumps(
                        {
                            "language": locale.steam_language,
                            "tagids": chunk,
                        },
                        separators=(",", ":"),
                    )
                },
            )
            payload = response.json()
            response_data = payload.get("response") if isinstance(payload, dict) else None
            rows = response_data.get("tags") if isinstance(response_data, dict) else None
            for raw in rows if isinstance(rows, list) else []:
                if not isinstance(raw, dict):
                    continue
                try:
                    tag_id = int(raw.get("tagid", raw.get("tag_id")))
                except (TypeError, ValueError):
                    continue
                name = raw.get("name") or raw.get("localized_name")
                if isinstance(name, str) and name.strip():
                    result[tag_id] = name.strip()
        return result

    async def pics_package_info(
        self,
        package_ids: Sequence[int],
    ) -> dict[int, dict[str, Any]]:
        """Try an explicitly configured anonymous PICS HTTP bridge.

        Steam's native anonymous ProductInfo transport is a client protocol,
        not a stable public HTTP endpoint.  Therefore no third-party host is
        silently selected: deployments may provide ``STEAM_PICS_API_URL``.
        """

        base_url = os.getenv("STEAM_PICS_API_URL", "").strip()
        if not base_url:
            raise SteamClientError("STEAM_PICS_API_URL is not configured")
        normalized = list(dict.fromkeys(int(package_id) for package_id in package_ids))
        if not normalized:
            return {}
        info_url = base_url.rstrip("/")
        if not info_url.casefold().endswith("/info"):
            info_url = f"{info_url}/info"
        response = await self._get(info_url, params={"packages": ",".join(map(str, normalized))})
        payload = response.json()
        if not isinstance(payload, dict):
            raise SteamClientError("Steam PICS bridge returned an unexpected response")
        raw_packages = payload.get("packages")
        if not isinstance(raw_packages, dict):
            error = payload.get("error")
            if isinstance(error, str) and error.strip():
                raise SteamClientError(error.strip())
            raise SteamClientError("Steam PICS bridge did not return package fields")
        result: dict[int, dict[str, Any]] = {}
        for raw_id, value in raw_packages.items():
            try:
                package_id = int(raw_id)
            except (TypeError, ValueError):
                continue
            if isinstance(value, dict):
                result[package_id] = value
        return result

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
                # Steam's supported StoreBrowse switch for structured tags.
                "include_tag_count": True,
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

    async def creator_home(self, creator_clan_account_id: int) -> str:
        """Fetch a public Creator Home page for one known clan identity."""

        response = await self._get(
            f"https://store.steampowered.com/curator/{creator_clan_account_id}/"
        )
        return response.text

    async def package_details(
        self,
        package_ids: Sequence[int],
        locale: LocaleInfo,
        *,
        store_country: str | None = None,
    ) -> dict[int, dict[str, Any]]:
        """Fetch public package metadata for bundle-only package placeholders."""

        normalized_ids = list(dict.fromkeys(int(package_id) for package_id in package_ids))
        if not normalized_ids:
            return {}
        result: dict[int, dict[str, Any]] = {}

        async def fetch_one(package_id: int) -> tuple[int, dict[str, Any] | None]:
            try:
                response = await self._get(
                    "https://store.steampowered.com/api/packagedetails",
                    params={
                        "packageids": str(package_id),
                        **self._localized_params(locale, store_country),
                    },
                )
                payload = response.json()
            except SteamClientError:
                return package_id, None
            if not isinstance(payload, dict):
                return package_id, None
            raw_entry = payload.get(str(package_id))
            if not isinstance(raw_entry, dict) or not raw_entry.get("success"):
                return package_id, None
            data = raw_entry.get("data")
            return package_id, data if isinstance(data, dict) else None

        for package_id, data in await asyncio.gather(
            *(fetch_one(package_id) for package_id in normalized_ids)
        ):
            if data is not None:
                result[package_id] = data
        return result
