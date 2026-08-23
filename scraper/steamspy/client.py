from __future__ import annotations

import asyncio
import re
import time
from collections.abc import Mapping
from typing import Any

import httpx

from scraper.wikidata.config import ScraperConfig

from .models import SteamSpyStats

STEAMSPY_API_URL = "https://steamspy.com/api.php"


class SteamSpyError(RuntimeError):
    """Raised when SteamSpy returns an unusable response."""


class SteamSpyClient:
    def __init__(
        self,
        client: httpx.AsyncClient,
        *,
        config: ScraperConfig | None = None,
    ) -> None:
        self.client = client
        self.config = config or ScraperConfig.from_env()
        self._rate_lock = asyncio.Lock()
        self._last_request = 0.0

    async def fetch_stats(self, app_id: int) -> SteamSpyStats:
        if app_id < 0:
            raise ValueError("Steam AppID cannot be negative")
        payload = await self._json(
            params={"request": "appdetails", "appid": app_id},
        )
        return parse_stats(payload, app_id=app_id)

    async def _json(self, *, params: Mapping[str, Any]) -> dict[str, Any]:
        response = await self._request(params=params)
        try:
            payload = response.json()
        except ValueError as exc:
            raise SteamSpyError("SteamSpy returned invalid JSON") from exc
        if not isinstance(payload, dict):
            raise SteamSpyError("SteamSpy returned an unexpected JSON payload")
        return payload

    async def _request(self, *, params: Mapping[str, Any]) -> httpx.Response:
        last_error: Exception | None = None
        headers = {"User-Agent": self.config.user_agent}
        for attempt in range(3):
            await self._wait_for_rate_limit()
            try:
                response = await self.client.get(
                    STEAMSPY_API_URL,
                    params=params,
                    headers=headers,
                    follow_redirects=True,
                )
                if 400 <= response.status_code < 500 and response.status_code != 429:
                    raise SteamSpyError(
                        f"SteamSpy returned HTTP {response.status_code}"
                    )
                if response.status_code == 429 or response.status_code >= 500:
                    if attempt < 2:
                        retry_after = response.headers.get("Retry-After")
                        await asyncio.sleep(min(float(retry_after or 1.0), 10.0))
                        continue
                response.raise_for_status()
                return response
            except (httpx.RequestError, httpx.HTTPStatusError, ValueError) as exc:
                last_error = exc
                if attempt < 2:
                    await asyncio.sleep(0.5 * (attempt + 1))
                    continue
                break
        raise SteamSpyError("SteamSpy request failed") from last_error

    async def _wait_for_rate_limit(self) -> None:
        async with self._rate_lock:
            now = time.monotonic()
            delay = self.config.steamspy_min_interval_seconds - (now - self._last_request)
            if delay > 0:
                await asyncio.sleep(delay)
            self._last_request = time.monotonic()


def parse_stats(payload: Mapping[str, Any], *, app_id: int) -> SteamSpyStats:
    raw_app_id = payload.get("appid")
    if isinstance(raw_app_id, str) and raw_app_id.isdigit():
        raw_app_id = int(raw_app_id)
    if raw_app_id is None:
        raise SteamSpyError(f"SteamSpy has no data for AppID {app_id}")
    if raw_app_id not in (app_id, None):
        raise SteamSpyError(
            f"SteamSpy returned AppID {raw_app_id!r} for requested AppID {app_id}"
        )
    if raw_app_id == 999999:
        raise SteamSpyError(f"SteamSpy data is hidden for AppID {app_id}")
    owners_min, owners_max = _owner_bounds(payload.get("owners"))
    return SteamSpyStats(
        app_id=app_id,
        owners_min=owners_min,
        owners_max=owners_max,
        average_forever_minutes=_integer(payload.get("average_forever")),
        average_two_weeks_minutes=_integer(payload.get("average_2weeks")),
        median_forever_minutes=_integer(payload.get("median_forever")),
        median_two_weeks_minutes=_integer(payload.get("median_2weeks")),
        ccu=_integer(payload.get("ccu")),
        tags=_tags(payload.get("tags")),
    )


def _owner_bounds(value: Any) -> tuple[int | None, int | None]:
    if not isinstance(value, str):
        return None, None
    numbers = [int(item.replace(",", "")) for item in re.findall(r"\d[\d,]*", value)]
    if len(numbers) < 2:
        return None, None
    return numbers[0], numbers[1]


def _integer(value: Any) -> int | None:
    if isinstance(value, bool) or value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError, OverflowError):
        return None


def _tags(value: Any) -> dict[str, int]:
    if not isinstance(value, Mapping):
        return {}
    return {
        str(name): votes
        for name, raw_votes in value.items()
        if (votes := _integer(raw_votes)) is not None
    }


__all__ = [
    "STEAMSPY_API_URL",
    "SteamSpyClient",
    "SteamSpyError",
    "parse_stats",
]
