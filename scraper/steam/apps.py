from __future__ import annotations

from typing import Any

from .client import SteamClient, SteamClientError


class SteamAppListError(RuntimeError):
    """Raised when Steam's official app catalog cannot be collected."""


_MAX_OFFICIAL_PAGE_SIZE = 50_000


def _append_ids(target: set[int], values: list[Any]) -> None:
    for value in values:
        if isinstance(value, dict):
            raw_app_id = value.get("appid")
        else:
            raw_app_id = value
        if raw_app_id is None:
            continue
        try:
            app_id = int(raw_app_id)
        except (TypeError, ValueError):
            continue
        if app_id > 0:
            target.add(app_id)


def _bounded_ids(app_ids: set[int], max_app_ids: int | None) -> list[int]:
    result = sorted(app_ids)
    return result[:max_app_ids] if max_app_ids is not None else result


async def _fetch_official_app_ids(
    client: SteamClient,
    *,
    api_key: str,
    max_app_ids: int | None,
    include_games: bool,
    include_dlc: bool,
    include_software: bool,
    include_videos: bool,
    include_hardware: bool,
) -> list[int]:
    app_ids: set[int] = set()
    last_appid: int | None = None
    while True:
        payload = await client.store_app_list_page(
            api_key=api_key,
            last_appid=last_appid,
            max_results=_MAX_OFFICIAL_PAGE_SIZE,
            include_games=include_games,
            include_dlc=include_dlc,
            include_software=include_software,
            include_videos=include_videos,
            include_hardware=include_hardware,
        )
        response = payload.get("response")
        if not isinstance(response, dict):
            raise SteamAppListError("Steam app list response has no response object")
        apps = response.get("apps")
        if not isinstance(apps, list):
            raise SteamAppListError("Steam app list response has no apps list")
        before = len(app_ids)
        _append_ids(app_ids, apps)
        if max_app_ids is not None and len(app_ids) >= max_app_ids:
            return _bounded_ids(app_ids, max_app_ids)
        if not apps:
            break

        next_last_appid = response.get("last_appid")
        if next_last_appid is None:
            next_last_appid = apps[-1].get("appid") if isinstance(apps[-1], dict) else None
        if next_last_appid is None:
            break
        try:
            next_last_appid = int(next_last_appid)
        except (TypeError, ValueError):
            break
        if last_appid is not None and next_last_appid <= last_appid:
            break
        if len(apps) < _MAX_OFFICIAL_PAGE_SIZE and not response.get("have_more_results", False):
            break
        if len(app_ids) == before:
            break
        last_appid = next_last_appid
    return _bounded_ids(app_ids, max_app_ids)


async def fetch_app_ids(
    client: SteamClient,
    *,
    api_key: str,
    max_app_ids: int | None = None,
    include_games: bool = True,
    include_dlc: bool = True,
    include_software: bool = True,
    include_videos: bool = True,
    include_hardware: bool = True,
) -> list[int]:
    if max_app_ids is not None and max_app_ids < 1:
        raise ValueError("max_app_ids must be positive")
    if not api_key.strip():
        raise ValueError("api_key must not be empty")
    try:
        return await _fetch_official_app_ids(
            client,
            api_key=api_key,
            max_app_ids=max_app_ids,
            include_games=include_games,
            include_dlc=include_dlc,
            include_software=include_software,
            include_videos=include_videos,
            include_hardware=include_hardware,
        )
    except SteamClientError as exc:
        raise SteamAppListError("Steam app IDs could not be collected") from exc
