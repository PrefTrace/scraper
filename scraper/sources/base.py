from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from decimal import Decimal
from typing import Any

from pydantic import BaseModel
from sqlalchemy import delete

from scraper.wikidata_deprecated.config import ScraperConfig
from scraper.wikidata_deprecated.orm import (
    ScraperDatabase,
    SourceDiagnostic,
    SourceFact,
    SourceRefresh,
    utcnow,
)


@dataclass(frozen=True, slots=True)
class SourceLoadError(Exception):
    status: str
    code: str
    message: str


class CachedSourceService:
    """Common async source refresh primitive; task ownership stays in pipeline."""

    source: str

    def __init__(
        self,
        database: ScraperDatabase,
        *,
        config: ScraperConfig | None = None,
    ) -> None:
        self.database = database
        self.config = config or database.config
        self.config.validate()
        self._schema_lock = asyncio.Lock()
        self._schema_ready = False
        self._locks: dict[tuple[int, str], asyncio.Lock] = {}
        self._write_lock = asyncio.Lock()

    async def ensure_schema(self) -> None:
        if self._schema_ready:
            return
        async with self._schema_lock:
            if not self._schema_ready:
                await self.database.create_schema()
                self._schema_ready = True

    async def _get_fresh(
        self,
        app_id: int,
        scope: str,
        *,
        force: bool,
    ) -> SourceRefresh | None:
        if force:
            return None
        cutoff = utcnow() - timedelta(seconds=self.config.ttl_seconds)
        async with self.database.session() as session:
            state = await session.get(
                SourceRefresh,
                {"source": self.source, "steam_app_id": app_id, "scope": scope},
            )
        if (
            state is None
            or state.checked_at is None
            or state.checked_at < cutoff
            or state.status not in {"ready", "not_found"}
        ):
            return None
        return state

    async def _refresh_scope(
        self,
        app_id: int,
        scope: str,
        loader: Callable[[], Awaitable[object]],
        *,
        force: bool,
    ) -> SourceRefresh:
        await self.ensure_schema()
        lock = self._locks.setdefault((app_id, scope), asyncio.Lock())
        async with lock:
            fresh = await self._get_fresh(app_id, scope, force=force)
            if fresh is not None:
                return fresh
            try:
                data = await loader()
            except SourceLoadError as exc:
                return await self._persist(
                    app_id,
                    scope,
                    data=None,
                    status=exc.status,
                    error=(exc.code, exc.message),
                )
            except Exception as exc:
                return await self._persist(
                    app_id,
                    scope,
                    data=None,
                    status="failed",
                    error=(type(exc).__name__, str(exc)),
                )
            return await self._persist(app_id, scope, data=data, status="ready")

    async def _persist(
        self,
        app_id: int,
        scope: str,
        *,
        data: object | None,
        status: str,
        error: tuple[str, str] | None = None,
    ) -> SourceRefresh:
        observed_at = utcnow()
        async with self._write_lock:
            async with self.database.session() as session:
                state = await session.get(
                    SourceRefresh,
                    {"source": self.source, "steam_app_id": app_id, "scope": scope},
                )
                if state is None:
                    state = SourceRefresh(
                        source=self.source,
                        steam_app_id=app_id,
                        scope=scope,
                    )
                    session.add(state)
                state.checked_at = observed_at
                state.status = status
                state.last_error = error[1] if error else None
                if status in {"ready", "not_found"}:
                    await session.execute(
                        delete(SourceFact).where(
                            SourceFact.source == self.source,
                            SourceFact.steam_app_id == app_id,
                            SourceFact.scope == scope,
                        )
                    )
                if status == "ready":
                    state.refreshed_at = observed_at
                    for path, value in _flatten(data):
                        session.add(
                            SourceFact(
                                source=self.source,
                                steam_app_id=app_id,
                                scope=scope,
                                path=path,
                                **_scalar_columns(value),
                            )
                        )
                if error:
                    session.add(
                        SourceDiagnostic(
                            source=self.source,
                            steam_app_id=app_id,
                            scope=scope,
                            code=error[0],
                            message=error[1],
                            observed_at=observed_at,
                        )
                    )
                await session.commit()
                return state


def _flatten(value: object | None, path: str = "") -> list[tuple[str, object]]:
    if value is None:
        return []
    if isinstance(value, BaseModel):
        return _flatten(value.model_dump(mode="python"), path)
    if isinstance(value, Mapping):
        result: list[tuple[str, object]] = []
        for key, item in value.items():
            child = f"{path}.{key}" if path else str(key)
            result.extend(_flatten(item, child))
        return result
    if isinstance(value, (list, tuple)):
        result = []
        for index, item in enumerate(value):
            result.extend(_flatten(item, f"{path}[{index}]"))
        return result
    return [(path or "value", value)]


def _scalar_columns(value: object) -> dict[str, Any]:
    if isinstance(value, bool):
        return {"value_type": "bool", "value_bool": value}
    if isinstance(value, int):
        return {"value_type": "int", "value_int": value}
    if isinstance(value, float):
        return {"value_type": "float", "value_float": value}
    if isinstance(value, Decimal):
        return {"value_type": "decimal", "value_text": str(value)}
    if isinstance(value, (datetime, date)):
        return {"value_type": "datetime", "value_text": value.isoformat()}
    return {"value_type": "text", "value_text": str(value)}


__all__ = ["CachedSourceService", "SourceLoadError"]
