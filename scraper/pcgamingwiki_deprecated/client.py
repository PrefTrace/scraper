from __future__ import annotations

import asyncio
import time
from collections.abc import Mapping, Sequence
from typing import Any
from urllib.parse import quote

import httpx

from scraper.wikidata.config import ScraperConfig

from .models import (
    CargoScalar,
    PCGamingWikiCargoField,
    PCGamingWikiCargoRow,
    PCGamingWikiGame,
)

PCGAMINGWIKI_API_URL = "https://www.pcgamingwiki.com/w/api.php"
PCGAMINGWIKI_ROOT_URL = "https://www.pcgamingwiki.com"

# These are page-scoped Cargo tables. Company and Engine are intentionally not
# included: they describe separate PCGamingWiki entities, not rows belonging to
# the game page itself.
CARGO_GAME_TABLES: tuple[str, ...] = (
    "Infobox_game",
    "GameData",
    "API",
    "Assignments",
    "Audio",
    "Availability",
    "Cloud",
    "Controller",
    "Input",
    "L10n",
    "Middleware",
    "Mods",
    "Multiplayer",
    "News",
    "SafeDisc",
    "StarForce",
    "Tags",
    "Taxonomy",
    "Video",
    "VR_support",
    "XDG",
)


class PCGamingWikiError(RuntimeError):
    """Raised when PCGamingWiki cannot provide usable Cargo data."""


class PCGamingWikiClient:
    """Read PCGamingWiki page data through Cargo only.

    The client intentionally does not call ``action=parse`` and does not parse
    wikitext. Cargo owns field names, types, list delimiters and page identity;
    this client only converts Cargo rows into ORM-ready current values.
    """

    def __init__(
        self,
        client: httpx.AsyncClient,
        *,
        config: ScraperConfig | None = None,
        game_tables: Sequence[str] = CARGO_GAME_TABLES,
    ) -> None:
        self.client = client
        self.config = config or ScraperConfig.from_env()
        self.game_tables = tuple(dict.fromkeys(game_tables))
        self._rate_lock = asyncio.Lock()
        self._last_request = 0.0
        self._table_cache: tuple[str, ...] | None = None
        self._field_cache: dict[str, list[PCGamingWikiCargoField]] = {}

    async def fetch_game(self, app_id: int, *, title: str | None = None) -> PCGamingWikiGame:
        if app_id < 0:
            raise ValueError("Steam AppID cannot be negative")
        available_tables = await self._available_game_tables()
        schemas = {table: await self._fields(table) for table in available_tables}
        infobox_rows = await self._query_by_app_id(
            "Infobox_game",
            schemas["Infobox_game"],
            app_id=app_id,
        )
        if not infobox_rows:
            suffix = f" ({title!r})" if title else ""
            raise PCGamingWikiError(
                f"PCGamingWiki Cargo has no Infobox_game row for Steam AppID {app_id}{suffix}"
            )

        page_id, page_title = _identity(infobox_rows[0])
        if page_id is None or page_title is None:
            raise PCGamingWikiError(
                f"PCGamingWiki Cargo returned incomplete page identity for Steam AppID {app_id}"
            )

        rows: list[PCGamingWikiCargoRow] = []
        for table in available_tables:
            table_rows = (
                infobox_rows
                if table == "Infobox_game"
                else await self._query_by_page_id(table, schemas[table], page_id)
            )
            rows.extend(
                _cargo_row(
                    table,
                    row,
                    fallback_page_id=page_id,
                    fallback_page_title=page_title,
                )
                for row in table_rows
            )
        return PCGamingWikiGame(
            steam_app_id=app_id,
            page_title=page_title,
            page_url=f"{PCGAMINGWIKI_ROOT_URL}/wiki/{quote(page_title.replace(' ', '_'))}",
            page_id=page_id,
            cargo_rows=rows,
        )

    async def _available_game_tables(self) -> tuple[str, ...]:
        if self._table_cache is not None:
            return self._table_cache
        payload = await self._json(
            params={"action": "cargotables", "format": "json"},
        )
        raw_tables = payload.get("cargotables")
        available = (
            {item for item in raw_tables if isinstance(item, str)}
            if isinstance(raw_tables, list)
            else set()
        )
        self._table_cache = tuple(table for table in self.game_tables if table in available)
        if "Infobox_game" not in self._table_cache:
            raise PCGamingWikiError("PCGamingWiki Cargo has no Infobox_game table")
        return self._table_cache

    async def _fields(self, table: str) -> list[PCGamingWikiCargoField]:
        cached = self._field_cache.get(table)
        if cached is not None:
            return cached
        payload = await self._json(
            params={"action": "cargofields", "table": table, "format": "json"},
        )
        raw_fields = payload.get("cargofields")
        if not isinstance(raw_fields, dict):
            raise PCGamingWikiError(f"PCGamingWiki Cargo returned no fields for {table}")
        fields: list[PCGamingWikiCargoField] = []
        for name, raw in raw_fields.items():
            if not isinstance(name, str) or not isinstance(raw, dict):
                continue
            raw_delimiter = raw.get("delimiter")
            fields.append(
                PCGamingWikiCargoField(
                    name=name,
                    field_type=str(raw.get("type", "String")),
                    is_list=_is_list_field(raw.get("isList")),
                    delimiter=(str(raw_delimiter) if isinstance(raw_delimiter, str) else ","),
                )
            )
        self._field_cache[table] = fields
        return fields

    async def _query_by_app_id(
        self,
        table: str,
        fields: Sequence[PCGamingWikiCargoField],
        *,
        app_id: int,
    ) -> list[dict[str, CargoScalar]]:
        return await self._query(
            table,
            fields,
            where=f'{table}.Steam_AppID HOLDS "{app_id}"',
        )

    async def _query_by_page_id(
        self,
        table: str,
        fields: Sequence[PCGamingWikiCargoField],
        page_id: int,
    ) -> list[dict[str, CargoScalar]]:
        return await self._query(
            table,
            fields,
            where=f'{table}._pageID = "{page_id}"',
        )

    async def _query(
        self,
        table: str,
        fields: Sequence[PCGamingWikiCargoField],
        *,
        where: str,
    ) -> list[dict[str, CargoScalar]]:
        query_fields = [f"{table}._pageID=PageID", f"{table}._pageName=Page"]
        query_fields.extend(f"{table}.{field.name}" for field in fields)
        payload = await self._json(
            params={
                "action": "cargoquery",
                "tables": table,
                "fields": ",".join(query_fields),
                "where": where,
                "limit": 500,
                "format": "json",
            },
        )
        raw_rows = payload.get("cargoquery")
        if not isinstance(raw_rows, list):
            raise PCGamingWikiError(f"PCGamingWiki Cargo returned no rows for {table}")
        result: list[dict[str, CargoScalar]] = []
        for raw_row in raw_rows:
            if not isinstance(raw_row, dict):
                continue
            raw_values = raw_row.get("fields", raw_row)
            if not isinstance(raw_values, dict):
                continue
            values: dict[str, CargoScalar] = {}
            for field in fields:
                if field.name in raw_values:
                    values[field.name] = _coerce_value(raw_values[field.name], field)
            for identity_name in ("PageID", "Page"):
                if identity_name in raw_values:
                    values[identity_name] = _coerce_identity(
                        raw_values[identity_name],
                        identity_name,
                    )
            result.append(values)
        return result

    async def _json(self, *, params: Mapping[str, Any]) -> dict[str, Any]:
        response = await self._request(params=params)
        try:
            payload = response.json()
        except ValueError as exc:
            raise PCGamingWikiError("PCGamingWiki Cargo returned invalid JSON") from exc
        if not isinstance(payload, dict):
            raise PCGamingWikiError("PCGamingWiki Cargo returned an unexpected payload")
        error = payload.get("error")
        if isinstance(error, dict):
            code = error.get("code", "unknown")
            info = error.get("info", "Cargo request failed")
            raise PCGamingWikiError(f"Cargo {code}: {info}")
        return payload

    async def _request(self, *, params: Mapping[str, Any]) -> httpx.Response:
        last_error: Exception | None = None
        headers = {"User-Agent": self.config.user_agent}
        for attempt in range(3):
            await self._wait_for_rate_limit()
            try:
                response = await self.client.get(
                    PCGAMINGWIKI_API_URL,
                    params=params,
                    headers=headers,
                    follow_redirects=True,
                )
                if 400 <= response.status_code < 500 and response.status_code != 429:
                    raise PCGamingWikiError(f"PCGamingWiki returned HTTP {response.status_code}")
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
        raise PCGamingWikiError("PCGamingWiki Cargo request failed") from last_error

    async def _wait_for_rate_limit(self) -> None:
        async with self._rate_lock:
            now = time.monotonic()
            delay = self.config.pcgamingwiki_min_interval_seconds - (now - self._last_request)
            if delay > 0:
                await asyncio.sleep(delay)
            self._last_request = time.monotonic()


def _is_list_field(value: object) -> bool:
    if isinstance(value, bool):
        return value
    return isinstance(value, str) and value.casefold() not in {"", "0", "false", "no"}


def _identity(row: Mapping[str, CargoScalar]) -> tuple[int | None, str | None]:
    page_id = row.get("PageID")
    page_title = row.get("Page")
    return (
        page_id if isinstance(page_id, int) else None,
        page_title if isinstance(page_title, str) else None,
    )


def _cargo_row(
    table: str,
    values: Mapping[str, CargoScalar],
    *,
    fallback_page_id: int,
    fallback_page_title: str,
) -> PCGamingWikiCargoRow:
    page_id, page_title = _identity(values)
    return PCGamingWikiCargoRow(
        table=table,
        page_id=page_id or fallback_page_id,
        page_title=page_title or fallback_page_title,
        values={key: value for key, value in values.items() if key not in {"PageID", "Page"}},
    )


def _coerce_identity(value: object, field_name: str) -> CargoScalar:
    if field_name == "PageID":
        try:
            return int(str(value))
        except (TypeError, ValueError):
            return None
    return str(value) if value is not None else None


def _coerce_value(value: object, field: PCGamingWikiCargoField) -> CargoScalar:
    if value is None:
        return None
    raw = str(value).strip()
    if not raw:
        return None
    if field.is_list:
        return [item.strip() for item in raw.split(field.delimiter) if item.strip()]
    field_type = field.field_type.casefold()
    if field_type in {"integer", "int"}:
        try:
            return int(raw)
        except ValueError:
            return raw
    if field_type in {"float", "number"}:
        try:
            return float(raw)
        except ValueError:
            return raw
    if field_type in {"boolean", "bool"}:
        if raw.casefold() in {"yes", "true", "1"}:
            return True
        if raw.casefold() in {"no", "false", "0"}:
            return False
    return raw


__all__ = [
    "CARGO_GAME_TABLES",
    "PCGAMINGWIKI_API_URL",
    "PCGamingWikiClient",
    "PCGamingWikiError",
]
