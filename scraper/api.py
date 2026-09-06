from __future__ import annotations

import httpx

from scraper.steam.apps import fetch_app_ids
from scraper.steam.client import SteamClient


async def get_app_ids(
    api_key: str,
    *,
    max_app_ids: int | None = None,
    include_games: bool = True,
    include_dlc: bool = True,
    include_software: bool = True,
    include_videos: bool = False,
    include_hardware: bool = False,
) -> list[int]:
    """Return unique Steam AppIDs from the existing official paginated catalog flow."""

    timeout = httpx.Timeout(35.0, connect=15.0)
    limits = httpx.Limits(max_connections=4, max_keepalive_connections=2)
    headers = {"User-Agent": "Mozilla/5.0 (compatible; game-scraper/1.0)"}
    async with httpx.AsyncClient(
        timeout=timeout,
        limits=limits,
        headers=headers,
        follow_redirects=True,
    ) as http:
        return await fetch_app_ids(
            SteamClient(http),
            api_key=api_key,
            max_app_ids=max_app_ids,
            include_games=include_games,
            include_dlc=include_dlc,
            include_software=include_software,
            include_videos=include_videos,
            include_hardware=include_hardware,
        )


__all__ = ["get_app_ids"]
