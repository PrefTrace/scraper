from __future__ import annotations

import asyncio
import re
import time
from collections.abc import Mapping
from typing import Any
from urllib.parse import unquote, urljoin

import httpx

from scraper.wikidata.config import ScraperConfig

from .models import PCGamingWikiGame

PCGAMINGWIKI_API_URL = "https://www.pcgamingwiki.com/w/api.php"
PCGAMINGWIKI_REDIRECT_URL = "https://www.pcgamingwiki.com/api/appid.php"
PCGAMINGWIKI_ROOT_URL = "https://www.pcgamingwiki.com"


class PCGamingWikiError(RuntimeError):
    """Raised when PCGamingWiki cannot provide a usable page."""


class PCGamingWikiClient:
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

    async def fetch_game(self, app_id: int, *, title: str | None = None) -> PCGamingWikiGame:
        if app_id < 0:
            raise ValueError("Steam AppID cannot be negative")
        page_title = await self._redirect_title(app_id)
        page: dict[str, Any] | None = None
        if page_title is None and title:
            resolved = await self._search_page(app_id, title)
            if resolved is not None:
                page_title, page = resolved
        if page_title is None:
            raise PCGamingWikiError(
                f"PCGamingWiki page was not resolved for Steam AppID {app_id}; title is required"
            )
        return await self._fetch_page(app_id, page_title, page=page)

    async def _redirect_title(self, app_id: int) -> str | None:
        try:
            response = await self._request(
                PCGAMINGWIKI_REDIRECT_URL,
                params={"appid": app_id},
                follow_redirects=False,
            )
        except PCGamingWikiError:
            return None
        if 300 <= response.status_code < 400:
            location = response.headers.get("Location")
            return _title_from_url(location) if location else None
        if response.status_code != 200:
            return None
        body = response.text.strip()
        if body.startswith("http"):
            return _title_from_url(body)
        return body if body and "not found" not in body.casefold() else None

    async def _search_page(
        self,
        app_id: int,
        title: str,
    ) -> tuple[str, dict[str, Any]] | None:
        payload = await self._json(
            PCGAMINGWIKI_API_URL,
            params={
                "action": "opensearch",
                "search": title,
                "redirects": "resolve",
                "namespace": 0,
                "limit": 8,
                "format": "json",
            },
        )
        candidates = payload[1] if isinstance(payload, list) and len(payload) > 1 else []
        if not isinstance(candidates, list):
            candidates = []
        for candidate in candidates:
            if not isinstance(candidate, str):
                continue
            try:
                page = await self._page(candidate)
            except PCGamingWikiError:
                continue
            if app_id in _steam_app_ids(page["wikitext"]):
                return candidate, page
        return None

    async def _fetch_page(
        self,
        app_id: int,
        page_title: str,
        *,
        page: dict[str, Any] | None = None,
    ) -> PCGamingWikiGame:
        payload = page or await self._page(page_title)
        wikitext = payload["wikitext"]
        app_ids = _steam_app_ids(wikitext)
        if app_id not in app_ids:
            raise PCGamingWikiError(
                f"PCGamingWiki page {page_title!r} does not contain Steam AppID {app_id}"
            )
        infobox = _parse_infobox(wikitext)
        sections = _parse_sections(wikitext)
        return PCGamingWikiGame(
            steam_app_id=app_id,
            page_title=page_title,
            page_url=urljoin(PCGAMINGWIKI_ROOT_URL, f"/wiki/{page_title.replace(' ', '_')}"),
            page_id=payload.get("page_id"),
            steam_app_ids=app_ids,
            cover_url=_first_field(infobox, "cover", "image", "cover image"),
            developers=_field_values(infobox, "developer", "developers"),
            publishers=_field_values(infobox, "publisher", "publishers"),
            engines=_field_values(infobox, "engine", "engines"),
            releases=_field_values(infobox, "release date", "release dates", "released"),
            external_ids=_external_ids(infobox),
            sections=sections,
            infobox=infobox,
        )

    async def _page(self, title: str) -> dict[str, Any]:
        payload = await self._json(
            PCGAMINGWIKI_API_URL,
            params={
                "action": "parse",
                "format": "json",
                "page": title,
                "redirects": 1,
                "prop": "wikitext|info",
            },
        )
        parse = payload.get("parse") if isinstance(payload, dict) else None
        if not isinstance(parse, dict):
            raise PCGamingWikiError(f"PCGamingWiki page {title!r} was not returned")
        wikitext = parse.get("wikitext")
        if isinstance(wikitext, dict):
            wikitext = wikitext.get("*")
        if not isinstance(wikitext, str):
            raise PCGamingWikiError(f"PCGamingWiki page {title!r} has no wikitext")
        page_id = parse.get("pageid")
        return {
            "wikitext": wikitext,
            "page_id": page_id if isinstance(page_id, int) else None,
        }

    async def _json(self, url: str, *, params: Mapping[str, Any]) -> list[Any] | dict[str, Any]:
        response = await self._request(url, params=params, follow_redirects=True)
        try:
            payload = response.json()
        except ValueError as exc:
            raise PCGamingWikiError("PCGamingWiki returned invalid JSON") from exc
        if not isinstance(payload, (dict, list)):
            raise PCGamingWikiError("PCGamingWiki returned an unexpected JSON payload")
        return payload

    async def _request(
        self,
        url: str,
        *,
        params: Mapping[str, Any],
        follow_redirects: bool,
    ) -> httpx.Response:
        last_error: Exception | None = None
        headers = {"User-Agent": self.config.user_agent}
        for attempt in range(3):
            await self._wait_for_rate_limit()
            try:
                response = await self.client.get(
                    url,
                    params=params,
                    headers=headers,
                    follow_redirects=follow_redirects,
                )
                if 400 <= response.status_code < 500 and response.status_code != 429:
                    raise PCGamingWikiError(
                        f"PCGamingWiki returned HTTP {response.status_code} for {url}"
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
        raise PCGamingWikiError(f"PCGamingWiki request failed: {url}") from last_error

    async def _wait_for_rate_limit(self) -> None:
        async with self._rate_lock:
            now = time.monotonic()
            delay = self.config.pcgamingwiki_min_interval_seconds - (now - self._last_request)
            if delay > 0:
                await asyncio.sleep(delay)
            self._last_request = time.monotonic()


def _title_from_url(value: str | None) -> str | None:
    if not value:
        return None
    match = re.search(r"/wiki/([^?#]+)", value)
    if not match:
        return None
    return unquote(match.group(1)).replace("_", " ").strip() or None


def _steam_app_ids(wikitext: str) -> list[int]:
    result: list[int] = []
    for key, value in _parse_infobox(wikitext).items():
        if "steam appid" not in key:
            continue
        for raw in re.findall(r"(?<!\d)(\d{1,12})(?!\d)", value):
            app_id = int(raw)
            if app_id not in result:
                result.append(app_id)
    return result


def _parse_infobox(wikitext: str) -> dict[str, str]:
    template = _extract_template(wikitext, "infobox game")
    if template is None:
        return {}
    fields: dict[str, str] = {}
    for part in _split_template_fields(template):
        if "=" not in part:
            continue
        key, value = part.split("=", 1)
        normalized_key = _clean_text(key).casefold().replace("_", " ")
        normalized_value = _clean_text(value)
        if normalized_key:
            fields[normalized_key] = normalized_value
    return fields


def _extract_template(text: str, template_name: str) -> str | None:
    match = re.search(r"\{\{\s*" + re.escape(template_name) + r"\b", text, re.IGNORECASE)
    if match is None:
        return None
    start = match.start()
    depth = 0
    index = start
    while index < len(text) - 1:
        token = text[index : index + 2]
        if token == "{{":
            depth += 1
            index += 2
            continue
        if token == "}}":
            depth -= 1
            if depth == 0:
                return text[match.end() : index]
            index += 2
            continue
        index += 1
    return None


def _split_template_fields(template: str) -> list[str]:
    result: list[str] = []
    start = 0
    depth = 0
    index = 0
    while index < len(template) - 1:
        token = template[index : index + 2]
        if token == "{{":
            depth += 1
            index += 2
            continue
        if token == "}}":
            depth = max(depth - 1, 0)
            index += 2
            continue
        if template[index] == "|" and depth == 0:
            result.append(template[start:index])
            start = index + 1
        index += 1
    result.append(template[start:])
    return result


def _clean_text(value: str) -> str:
    value = re.sub(r"<!--.*?-->", "", value, flags=re.DOTALL)
    value = re.sub(r"<br\s*/?>", "\n", value, flags=re.IGNORECASE)
    value = re.sub(
        r"\{\{\s*release\s+date\s*\|([^{}]+)\}\}",
        lambda match: " ".join(
            part.strip()
            for part in match.group(1).split("|")
            if part.strip() and "=" not in part
        ),
        value,
        flags=re.IGNORECASE,
    )
    value = re.sub(r"\[\[([^\]|]+)\|([^\]]+)\]\]", r"\2", value)
    value = re.sub(r"\[\[([^\]]+)\]\]", r"\1", value)
    value = re.sub(r"\[https?://[^\s\]]+\s+([^\]]+)\]", r"\1", value)
    value = re.sub(r"\{\{[^{}]*\}\}", "", value)
    value = re.sub(r"'''?", "", value)
    return re.sub(r"\s+", " ", value).strip()


def _field_values(fields: Mapping[str, str], *names: str) -> list[str]:
    values: list[str] = []
    for name in names:
        raw = fields.get(name)
        if not raw:
            continue
        for value in re.split(r"\n|;|\s+\|\s+", raw):
            value = _clean_text(value).strip(" -*")
            if value and value not in values:
                values.append(value)
    return values


def _first_field(fields: Mapping[str, str], *names: str) -> str | None:
    for name in names:
        value = fields.get(name)
        if value:
            return value
    return None


def _external_ids(fields: Mapping[str, str]) -> dict[str, str]:
    names = (
        "official site",
        "gogcom",
        "gog",
        "lutris",
        "mobygames",
        "wikipedia",
        "winehq",
        "strategywiki",
        "hltb",
        "pcgamingwiki",
    )
    return {name: fields[name] for name in names if fields.get(name)}


def _parse_sections(wikitext: str) -> dict[str, str]:
    sections: dict[str, list[str]] = {}
    current = "lead"
    sections[current] = []
    for line in wikitext.splitlines():
        heading = re.match(r"^(={2,6})\s*(.+?)\s*\1\s*$", line)
        if heading:
            current = _clean_text(heading.group(2)) or current
            sections.setdefault(current, [])
            continue
        sections.setdefault(current, []).append(line)
    return {
        title: _clean_text("\n".join(lines))
        for title, lines in sections.items()
        if _clean_text("\n".join(lines))
    }


__all__ = [
    "PCGAMINGWIKI_API_URL",
    "PCGAMINGWIKI_REDIRECT_URL",
    "PCGamingWikiClient",
    "PCGamingWikiError",
    "_parse_infobox",
    "_steam_app_ids",
]
