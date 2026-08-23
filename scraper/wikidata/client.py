from __future__ import annotations

import asyncio
from collections.abc import Iterable, Mapping
from typing import Any

import httpx

WIKIDATA_API_URL = "https://www.wikidata.org/w/api.php"
WIKIDATA_SPARQL_URL = "https://query.wikidata.org/sparql"
STEAM_APP_ID_PROPERTY = "P1733"
WIKIDATA_USER_AGENT = (
    "game-scraper/1.0 (https://www.wikidata.org/wiki/Wikidata:Data_access)"
)


class WikidataError(RuntimeError):
    """Raised when a Wikibase endpoint returns an unusable response."""


JsonObject = dict[str, Any]


class WikidataClient:
    """Async low-level client used by the ORM synchronizer."""

    def __init__(
        self,
        client: httpx.AsyncClient,
        *,
        user_agent: str = WIKIDATA_USER_AGENT,
    ) -> None:
        self.client = client
        self.user_agent = user_agent

    async def _get_json(
        self,
        url: str,
        *,
        params: Mapping[str, Any],
        accept: str | None = None,
    ) -> JsonObject:
        last_error: Exception | None = None
        headers = {"User-Agent": self.user_agent}
        if accept:
            headers["Accept"] = accept
        for attempt in range(3):
            try:
                response = await self.client.get(url, params=params, headers=headers)
                if response.status_code == 429 or response.status_code >= 500:
                    if attempt < 2:
                        retry_after = response.headers.get("Retry-After")
                        delay = min(float(retry_after or (0.5 * (attempt + 1))), 3.0)
                        await asyncio.sleep(delay)
                        continue
                response.raise_for_status()
                payload = response.json()
                if not isinstance(payload, dict):
                    raise WikidataError(f"Unexpected JSON object from {url}")
                return payload
            except (httpx.RequestError, httpx.HTTPStatusError, ValueError, WikidataError) as exc:
                last_error = exc
                if attempt < 2:
                    await asyncio.sleep(0.5 * (attempt + 1))
                    continue
                break
        raise WikidataError(f"Wikidata request failed: {url}") from last_error

    async def find_by_steam_app_id(self, app_id: int) -> list[str]:
        query = f"""
SELECT DISTINCT ?item WHERE {{
  ?item wdt:{STEAM_APP_ID_PROPERTY} ?steamAppId .
  FILTER(STR(?steamAppId) = \"{app_id}\")
}}
LIMIT 10
"""
        payload = await self._get_json(
            WIKIDATA_SPARQL_URL,
            params={"query": query, "format": "json"},
            accept="application/sparql-results+json",
        )
        results = payload.get("results")
        bindings = results.get("bindings") if isinstance(results, dict) else None
        if not isinstance(bindings, list):
            raise WikidataError("Unexpected Wikidata SPARQL result")
        item_ids: list[str] = []
        for binding in bindings:
            if not isinstance(binding, dict):
                continue
            item = binding.get("item")
            if not isinstance(item, dict):
                continue
            value = item.get("value")
            qid = value.rsplit("/", 1)[-1] if isinstance(value, str) else ""
            if qid.startswith("Q"):
                item_ids.append(qid)
        return list(dict.fromkeys(item_ids))

    async def search_entities(self, name: str, *, limit: int = 10) -> list[str]:
        """Find Wikidata item IDs by a human-readable name."""

        payload = await self._get_json(
            WIKIDATA_API_URL,
            params={
                "action": "wbsearchentities",
                "format": "json",
                "formatversion": 2,
                "search": name,
                "language": "en",
                "uselang": "en",
                "type": "item",
                "limit": limit,
            },
        )
        results = payload.get("search")
        if not isinstance(results, list):
            raise WikidataError("Unexpected Wikibase search result")
        item_ids: list[str] = []
        for result in results:
            if not isinstance(result, dict):
                continue
            qid = result.get("id")
            if isinstance(qid, str) and qid.startswith("Q"):
                item_ids.append(qid)
        return list(dict.fromkeys(item_ids))

    async def get_entities(
        self,
        ids: Iterable[str],
        *,
        full: bool,
    ) -> dict[str, JsonObject]:
        unique_ids = sorted({item for item in ids if item})
        if not unique_ids:
            return {}
        props = "claims|labels|descriptions|aliases" if full else "info|labels|descriptions|aliases"
        result: dict[str, JsonObject] = {}
        for index in range(0, len(unique_ids), 50):
            chunk = unique_ids[index : index + 50]
            payload = await self._get_json(
                WIKIDATA_API_URL,
                params={
                    "action": "wbgetentities",
                    "format": "json",
                    "formatversion": 2,
                    "ids": "|".join(chunk),
                    "props": props,
                    "languages": "en|ru",
                    "languagefallback": 1,
                },
            )
            entities = payload.get("entities")
            if not isinstance(entities, (dict, list)):
                raise WikidataError("Unexpected Wikibase entities response")
            iterable = entities.values() if isinstance(entities, dict) else entities
            for entity in iterable:
                if isinstance(entity, dict) and isinstance(entity.get("id"), str):
                    result[entity["id"]] = entity
        return result


def _item_id(value: Any) -> str | None:
    if not isinstance(value, dict):
        return None
    item_id = value.get("id")
    if isinstance(item_id, str) and item_id.startswith(("Q", "P")):
        return item_id
    numeric_id = value.get("numeric-id")
    if isinstance(numeric_id, int):
        return f"Q{numeric_id}"
    return None


def _snak_value(snak: Mapping[str, Any]) -> Any:
    if snak.get("snaktype") not in (None, "value"):
        return None
    datavalue = snak.get("datavalue")
    return datavalue.get("value") if isinstance(datavalue, dict) else None


def _language_value(values: Mapping[str, Any]) -> str | None:
    for language in ("en", "ru", "mul"):
        value = values.get(language)
        text = value.get("value") if isinstance(value, dict) else None
        if isinstance(text, str):
            return text
    for value in values.values():
        text = value.get("value") if isinstance(value, dict) else None
        if isinstance(text, str):
            return text
    return None


def _formatter_url(property_entity: Mapping[str, Any]) -> str | None:
    claims = property_entity.get("claims")
    if not isinstance(claims, dict):
        return None
    statements = claims.get("P1630")
    if not isinstance(statements, list):
        return None
    for statement in statements:
        if not isinstance(statement, dict) or statement.get("rank") == "deprecated":
            continue
        mainsnak = statement.get("mainsnak")
        if not isinstance(mainsnak, dict):
            continue
        value = _snak_value(mainsnak)
        if isinstance(value, str) and value:
            return value
    return None


__all__ = [
    "STEAM_APP_ID_PROPERTY",
    "WIKIDATA_API_URL",
    "WIKIDATA_SPARQL_URL",
    "WIKIDATA_USER_AGENT",
    "WikidataClient",
    "WikidataError",
]
