from __future__ import annotations

import asyncio
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

import httpx
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from .client import (
    WikidataClient,
    _formatter_url,
    _item_id,
    _language_value,
    _snak_value,
)
from .config import ScraperConfig
from .orm import (
    ScraperDatabase,
    WikidataAlias,
    WikidataEntity,
    WikidataFact,
    WikidataGame,
    WikidataGameLink,
    WikidataNameLookup,
    WikidataNameLookupResult,
    WikidataQualifier,
    utcnow,
)

_FULL_GAME_PROPERTIES = {
    "P178",
    "P123",
    "P144",
    "P170",
    "P57",
    "P50",
    "P287",
    "P86",
    "P162",
    "P943",
    "P3080",
    "P767",
    "P725",
}


class WikidataSyncError(RuntimeError):
    """Raised when an ORM refresh cannot be completed."""


@dataclass(frozen=True, slots=True)
class WikidataGameResult:
    """Result of the single-game Wikidata queue task."""

    app_id: int
    item_qid: str | None
    status: str
    links: dict[str, set[str]]


@dataclass(frozen=True, slots=True)
class WikidataOrganizationNameResult:
    """Result of a name-resolution task; full entity loading is separate."""

    name: str
    qids: list[str]


class WikidataSyncService:
    """Refresh Wikidata facts into SQLite while reusing fresh ORM records."""

    def __init__(
        self,
        database: ScraperDatabase,
        *,
        config: ScraperConfig | None = None,
    ) -> None:
        self.database = database
        self.config = config or database.config
        self.config.validate()
        self._semaphore = asyncio.Semaphore(self.config.concurrency)
        self._schema_lock = asyncio.Lock()
        self._schema_ready = False
        self._app_locks: dict[int, asyncio.Lock] = {}
        self._entity_locks: dict[str, asyncio.Lock] = {}
        self._name_locks: dict[str, asyncio.Lock] = {}
        self._database_write_lock = asyncio.Lock()

    async def ensure_schema(self) -> None:
        if self._schema_ready:
            return
        async with self._schema_lock:
            if not self._schema_ready:
                await self.database.create_schema()
                self._schema_ready = True

    async def refresh_game_task(
        self,
        app_id: int,
        *,
        force: bool = False,
        client: httpx.AsyncClient | None = None,
    ) -> WikidataGameResult:
        """Refresh only the game entity; related entities become new tasks."""

        if app_id < 0:
            raise ValueError("Steam AppID cannot be negative")
        await self.ensure_schema()
        lock = self._app_locks.setdefault(app_id, asyncio.Lock())
        async with lock:
            if client is not None:
                return await self._refresh_game_task_with_client(client, app_id, force=force)
            timeout = httpx.Timeout(
                self.config.request_timeout_seconds,
                connect=self.config.connect_timeout_seconds,
            )
            limits = httpx.Limits(
                max_connections=self.config.concurrency,
                max_keepalive_connections=max(2, self.config.concurrency // 2),
            )
            async with httpx.AsyncClient(
                timeout=timeout,
                limits=limits,
                headers={
                    "User-Agent": self.config.user_agent,
                    "Accept-Language": "en,ru;q=0.8",
                },
                follow_redirects=True,
            ) as http:
                return await self._refresh_game_task_with_client(http, app_id, force=force)

    async def _refresh_game_task_with_client(
        self,
        http: httpx.AsyncClient,
        app_id: int,
        *,
        force: bool,
    ) -> WikidataGameResult:
        client = WikidataClient(http, user_agent=self.config.user_agent)
        now = utcnow()
        game = await self._get_game(app_id)
        lookup_fresh = (
            not force
            and game is not None
            and game.lookup_at is not None
            and game.lookup_at >= self._cutoff(now)
        )
        root_qid = game.item_qid if lookup_fresh and game is not None else None
        if not lookup_fresh:
            item_ids = await client.find_by_steam_app_id(app_id)
            root_qid = item_ids[0] if item_ids else None

        if root_qid is None:
            await self._save_game(
                app_id,
                None,
                lookup_at=now,
                refreshed_at=None,
                links={},
                status="not_found",
                last_error="No Wikidata item mapped to Steam AppID",
            )
            return WikidataGameResult(app_id, None, "not_found", {})

        await self._ensure_entities(
            client,
            {root_qid},
            full_ids={root_qid},
            force=force,
        )
        developer_ids = await self._item_ids_for_properties({root_qid}, {"P178"})
        publisher_ids = await self._item_ids_for_properties({root_qid}, {"P123"})
        related_ids = await self._full_game_ids(root_qid)
        links = {
            "root": {root_qid},
            "wikidata_developer": developer_ids,
            "wikidata_publisher": publisher_ids,
            "related": related_ids - developer_ids - publisher_ids - {root_qid},
        }
        await self._save_game(
            app_id,
            root_qid,
            lookup_at=now,
            refreshed_at=utcnow(),
            links=links,
        )
        return WikidataGameResult(app_id, root_qid, "ready", links)

    async def refresh_organization_name(
        self,
        name: str,
        *,
        force: bool = False,
        client: httpx.AsyncClient | None = None,
    ) -> WikidataOrganizationNameResult:
        """Resolve one organization name without loading its full entity."""

        if not name.strip():
            return WikidataOrganizationNameResult(name, [])
        await self.ensure_schema()
        if client is not None:
            wikidata = WikidataClient(client, user_agent=self.config.user_agent)
            qids = await self._resolve_name(wikidata, name, force=force)
            return WikidataOrganizationNameResult(name, qids)
        timeout = httpx.Timeout(
            self.config.request_timeout_seconds,
            connect=self.config.connect_timeout_seconds,
        )
        async with httpx.AsyncClient(
            timeout=timeout,
            headers={"User-Agent": self.config.user_agent},
            follow_redirects=True,
        ) as http:
            wikidata = WikidataClient(http, user_agent=self.config.user_agent)
            qids = await self._resolve_name(wikidata, name, force=force)
            return WikidataOrganizationNameResult(name, qids)

    async def refresh_entity(
        self,
        qid: str,
        *,
        force: bool = False,
        client: httpx.AsyncClient | None = None,
    ) -> WikidataEntity | None:
        """Refresh exactly one Wikidata entity."""

        if not qid.startswith(("Q", "P")):
            raise ValueError(f"Invalid Wikidata entity ID: {qid}")
        await self.ensure_schema()
        if client is not None:
            wikidata = WikidataClient(client, user_agent=self.config.user_agent)
            await self._ensure_entities(
                wikidata,
                {qid},
                full_ids={qid},
                force=force,
            )
        else:
            timeout = httpx.Timeout(
                self.config.request_timeout_seconds,
                connect=self.config.connect_timeout_seconds,
            )
            async with httpx.AsyncClient(
                timeout=timeout,
                headers={"User-Agent": self.config.user_agent},
                follow_redirects=True,
            ) as http:
                wikidata = WikidataClient(http, user_agent=self.config.user_agent)
                await self._ensure_entities(
                    wikidata,
                    {qid},
                    full_ids={qid},
                    force=force,
                )
        async with self.database.session() as session:
            return await session.get(WikidataEntity, qid)

    async def link_game_entity(self, app_id: int, qid: str, *, relation: str) -> None:
        """Persist one current game-to-entity relation."""

        await self.ensure_schema()
        async with self._database_write_lock:
            async with self.database.session() as session:
                game = await session.get(WikidataGame, app_id)
                if game is None:
                    game = WikidataGame(
                        steam_app_id=app_id,
                        item_qid=None,
                        status="pending",
                    )
                    session.add(game)
                    await session.flush()
                entity = await session.get(WikidataEntity, qid)
                if entity is None:
                    session.add(
                        WikidataEntity(
                            qid=qid,
                            entity_kind="property" if qid.startswith("P") else "item",
                        )
                    )
                    await session.flush()
                existing = await session.scalar(
                    select(WikidataGameLink).where(
                        WikidataGameLink.steam_app_id == app_id,
                        WikidataGameLink.qid == qid,
                        WikidataGameLink.relation == relation,
                    )
                )
                if existing is None:
                    session.add(
                        WikidataGameLink(
                            steam_app_id=app_id,
                            qid=qid,
                            relation=relation,
                            observed_at=utcnow(),
                        )
                    )
                await session.commit()

    async def _ensure_entities(
        self,
        client: WikidataClient,
        entity_ids: Iterable[str],
        *,
        full_ids: set[str],
        force: bool,
    ) -> None:
        unique_ids = {qid for qid in entity_ids if qid}
        if not unique_ids:
            return
        now = utcnow()
        async with self.database.session() as session:
            rows = {
                entity.qid: entity
                for entity in (
                    await session.scalars(
                        select(WikidataEntity).where(WikidataEntity.qid.in_(unique_ids))
                    )
                ).all()
            }
        stale_full = {
            qid
            for qid in full_ids & unique_ids
            if force or not _is_fresh(rows.get(qid), full=True, cutoff=self._cutoff(now))
        }
        stale_labels = {
            qid
            for qid in unique_ids - full_ids
            if force or not _is_fresh(rows.get(qid), full=False, cutoff=self._cutoff(now))
        }
        batches = [(batch, True) for batch in _chunks(stale_full, self.config.batch_size)] + [
            (batch, False) for batch in _chunks(stale_labels, self.config.batch_size)
        ]
        if not batches:
            return

        async def fetch_batch(batch: list[str], full: bool) -> None:
            locks = [self._entity_locks.setdefault(qid, asyncio.Lock()) for qid in batch]
            for lock in locks:
                await lock.acquire()
            try:
                recheck_at = utcnow()
                async with self.database.session() as session:
                    rows = {
                        entity.qid: entity
                        for entity in (
                            await session.scalars(
                                select(WikidataEntity).where(WikidataEntity.qid.in_(batch))
                            )
                        ).all()
                    }
                pending = [
                    qid
                    for qid in batch
                    if force
                    or not _is_fresh(rows.get(qid), full=full, cutoff=self._cutoff(recheck_at))
                ]
                if not pending:
                    return
                async with self._semaphore:
                    payloads = await client.get_entities(pending, full=full)
                async with self._database_write_lock:
                    async with self.database.session() as session:
                        for payload in payloads.values():
                            await _save_entity_payload(
                                session,
                                payload,
                                full=full,
                                observed_at=recheck_at,
                            )
                        await session.commit()
            finally:
                for lock in reversed(locks):
                    lock.release()

        await asyncio.gather(*(fetch_batch(batch, full) for batch, full in batches))

    async def _full_game_ids(self, root_qid: str) -> set[str]:
        return await self._item_ids_for_properties({root_qid}, _FULL_GAME_PROPERTIES)

    async def _item_ids_for_properties(
        self,
        subject_ids: set[str],
        property_ids: set[str],
    ) -> set[str]:
        if not subject_ids or not property_ids:
            return set()
        async with self.database.session() as session:
            values = (
                await session.scalars(
                    select(WikidataFact.value_qid).where(
                        WikidataFact.subject_qid.in_(subject_ids),
                        WikidataFact.property_id.in_(property_ids),
                        WikidataFact.value_type == "item",
                        WikidataFact.value_qid.is_not(None),
                    )
                )
            ).all()
        return {value for value in values if value is not None}

    async def _resolve_name(
        self,
        client: WikidataClient,
        name: str,
        *,
        force: bool,
    ) -> list[str]:
        normalized_name = _normalize_name(name)
        if not normalized_name:
            return []
        lock = self._name_locks.setdefault(normalized_name, asyncio.Lock())
        async with lock:
            now = utcnow()
            async with self.database.session() as session:
                lookup = await session.get(WikidataNameLookup, normalized_name)
                if not force and lookup is not None and lookup.searched_at >= self._cutoff(now):
                    values = (
                        await session.scalars(
                            select(WikidataNameLookupResult.qid).where(
                                WikidataNameLookupResult.normalized_name == normalized_name
                            )
                        )
                    ).all()
                    return list(values)

            async with self._semaphore:
                candidates = await client.search_entities(name, limit=10)
            # wbsearchentities returns ranked candidates. Keep the best current
            # candidate only; the lookup itself is refreshed as one current fact.
            candidates = list(dict.fromkeys(candidates[:1]))

            observed_at = utcnow()
            async with self._database_write_lock:
                async with self.database.session() as session:
                    lookup = await session.get(WikidataNameLookup, normalized_name)
                    if lookup is None:
                        lookup = WikidataNameLookup(
                            normalized_name=normalized_name,
                            searched_name=name,
                            searched_at=observed_at,
                        )
                        session.add(lookup)
                    else:
                        lookup.searched_name = name
                        lookup.searched_at = observed_at
                    await session.execute(
                        delete(WikidataNameLookupResult).where(
                            WikidataNameLookupResult.normalized_name == normalized_name
                        )
                    )
                    for qid in candidates:
                        session.add(
                            WikidataNameLookupResult(
                                normalized_name=normalized_name,
                                qid=qid,
                            )
                        )
                    await session.commit()
            return candidates

    async def _get_game(self, app_id: int) -> WikidataGame | None:
        async with self.database.session() as session:
            return await session.get(WikidataGame, app_id)

    async def _save_game(
        self,
        app_id: int,
        root_qid: str | None,
        *,
        lookup_at: datetime,
        refreshed_at: datetime | None,
        links: dict[str, set[str]],
        status: str = "ready",
        last_error: str | None = None,
    ) -> WikidataGame:
        async with self.database.session() as session:
            game = await session.get(WikidataGame, app_id)
            if game is None:
                game = WikidataGame(steam_app_id=app_id, item_qid=root_qid)
                session.add(game)
            else:
                game.item_qid = root_qid
            game.lookup_at = lookup_at
            game.refreshed_at = refreshed_at
            game.status = status
            game.last_error = last_error
            await session.flush()
            relations_to_replace = {
                "root",
                "linked",
                "full",
                "organization",
                "related",
                "wikidata_developer",
                "wikidata_publisher",
            } | set(links)
            await session.execute(
                delete(WikidataGameLink).where(
                    WikidataGameLink.steam_app_id == app_id,
                    WikidataGameLink.relation.in_(relations_to_replace),
                )
            )
            for relation, qids in links.items():
                for qid in qids:
                    entity = await session.get(WikidataEntity, qid)
                    if entity is None:
                        session.add(
                            WikidataEntity(
                                qid=qid,
                                entity_kind="property" if qid.startswith("P") else "item",
                            )
                        )
                        await session.flush()
                    session.add(
                        WikidataGameLink(
                            steam_app_id=app_id,
                            qid=qid,
                            relation=relation,
                            observed_at=refreshed_at or lookup_at,
                        )
                    )
            await session.commit()
            return game

    def _cutoff(self, now: datetime) -> datetime:
        return now - timedelta(seconds=self.config.ttl_seconds)


async def _save_entity_payload(
    session: AsyncSession,
    payload: Mapping[str, Any],
    *,
    full: bool,
    observed_at: datetime,
) -> WikidataEntity:
    qid = payload.get("id")
    if not isinstance(qid, str) or not qid:
        raise WikidataSyncError("Wikibase payload has no entity id")
    entity = await session.get(WikidataEntity, qid)
    if entity is None:
        entity = WikidataEntity(qid=qid, entity_kind="property" if qid.startswith("P") else "item")
        session.add(entity)
        await session.flush()
    labels = payload.get("labels") if isinstance(payload.get("labels"), dict) else {}
    descriptions = (
        payload.get("descriptions") if isinstance(payload.get("descriptions"), dict) else {}
    )
    if isinstance(payload.get("labels"), dict):
        if labels:
            entity.label = _language_value(labels)
        entity.labels_fetched_at = observed_at
    if descriptions:
        entity.description = _language_value(descriptions)
    if isinstance(payload.get("datatype"), str):
        entity.datatype = payload["datatype"]
    if full and qid.startswith("P"):
        entity.formatter_url = _formatter_url(dict(payload))
    source_revision = payload.get("lastrevid")
    if isinstance(source_revision, int):
        entity.source_revision = source_revision
    if full and "claims" in payload:
        entity.full_fetched_at = observed_at
        await session.execute(
            delete(WikidataQualifier).where(
                WikidataQualifier.fact_id.in_(
                    select(WikidataFact.id).where(WikidataFact.subject_qid == qid)
                )
            )
        )
        await session.execute(delete(WikidataFact).where(WikidataFact.subject_qid == qid))
        claims = payload.get("claims")
        if isinstance(claims, dict):
            for property_id, statements in claims.items():
                if not isinstance(property_id, str) or not isinstance(statements, list):
                    continue
                for statement in statements:
                    if not isinstance(statement, dict):
                        continue
                    fact = _fact_from_statement(
                        qid,
                        property_id,
                        statement,
                        observed_at=observed_at,
                        source_revision=(
                            source_revision if isinstance(source_revision, int) else None
                        ),
                    )
                    session.add(fact)
    aliases = payload.get("aliases")
    if isinstance(aliases, dict):
        await session.execute(delete(WikidataAlias).where(WikidataAlias.qid == qid))
        for language, values in aliases.items():
            if not isinstance(language, str) or not isinstance(values, list):
                continue
            for value in values:
                if isinstance(value, dict) and isinstance(value.get("value"), str):
                    session.add(WikidataAlias(qid=qid, language=language, alias=value["value"]))
    entity.updated_at = observed_at
    return entity


def _fact_from_statement(
    subject_qid: str,
    property_id: str,
    statement: Mapping[str, Any],
    *,
    observed_at: datetime,
    source_revision: int | None,
) -> WikidataFact:
    mainsnak = statement.get("mainsnak")
    columns = _snak_columns(mainsnak if isinstance(mainsnak, dict) else {})
    fact = WikidataFact(
        subject_qid=subject_qid,
        property_id=property_id,
        rank=(
            statement.get("rank", "normal") if isinstance(statement.get("rank"), str) else "normal"
        ),
        observed_at=observed_at,
        source_revision=source_revision,
        **columns,
    )
    qualifiers = statement.get("qualifiers")
    if isinstance(qualifiers, dict):
        for qualifier_property, values in qualifiers.items():
            if not isinstance(qualifier_property, str) or not isinstance(values, list):
                continue
            for value in values:
                if isinstance(value, dict):
                    fact.qualifiers.append(
                        WikidataQualifier(
                            property_id=qualifier_property,
                            **_snak_columns(value),
                        )
                    )
    return fact


def _snak_columns(snak: Mapping[str, Any]) -> dict[str, Any]:
    snak_type = snak.get("snaktype")
    if snak_type not in (None, "value"):
        return {
            "value_type": snak_type,
            "value_qid": None,
            "value_text": None,
            "value_amount": None,
            "value_unit": None,
            "value_time": None,
            "value_precision": None,
            "value_calendar_model": None,
        }
    value = _snak_value(dict(snak))
    if isinstance(value, dict):
        item_id = _item_id(value)
        if item_id:
            return _empty_columns("item", value_qid=item_id)
        if isinstance(value.get("amount"), str):
            unit = value.get("unit")
            return _empty_columns(
                "quantity",
                value_amount=value["amount"],
                value_unit=_uri_tail(unit),
            )
        if isinstance(value.get("time"), str):
            return _empty_columns(
                "time",
                value_time=_normalize_time(value["time"]),
                value_precision=value.get("precision")
                if isinstance(value.get("precision"), int)
                else None,
                value_calendar_model=_uri_tail(value.get("calendarmodel")),
            )
        if isinstance(value.get("text"), str):
            return _empty_columns("monolingualtext", value_text=value["text"])
    if isinstance(value, str):
        return _empty_columns("string", value_text=value)
    return _empty_columns("unknown")


def _empty_columns(value_type: str, **values: Any) -> dict[str, Any]:
    result: dict[str, Any] = {
        "value_type": value_type,
        "value_qid": None,
        "value_text": None,
        "value_amount": None,
        "value_unit": None,
        "value_time": None,
        "value_precision": None,
        "value_calendar_model": None,
    }
    result.update(values)
    return result


def _uri_tail(value: Any) -> str | None:
    return value.rsplit("/", 1)[-1] if isinstance(value, str) else None


def _normalize_time(value: str) -> str:
    normalized = value.lstrip("+")
    if "T" in normalized:
        normalized = normalized.split("T", 1)[0]
    if normalized.endswith("-00-00"):
        return normalized[:-6]
    if normalized.endswith("-00"):
        return normalized[:-3]
    return normalized


def _is_fresh(
    entity: WikidataEntity | None,
    *,
    full: bool,
    cutoff: datetime,
) -> bool:
    if entity is None:
        return False
    fetched_at = entity.full_fetched_at if full else entity.labels_fetched_at
    return fetched_at is not None and fetched_at >= cutoff


def _chunks(values: Iterable[str], size: int) -> list[list[str]]:
    ordered = sorted(set(values))
    return [ordered[index : index + size] for index in range(0, len(ordered), size)]


def _normalize_name(value: str) -> str:
    return " ".join(value.casefold().split())


__all__ = [
    "WikidataGameResult",
    "WikidataOrganizationNameResult",
    "WikidataSyncError",
    "WikidataSyncService",
]
