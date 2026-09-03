from __future__ import annotations

import httpx

from scraper.metacritic_deprecated.client import _load_metacritic
from scraper.wikidata.orm import SourceRefresh

from .base import CachedSourceService, SourceLoadError


class MetacriticSyncService(CachedSourceService):
    """Legacy Metacritic synchronizer kept outside the active pipeline."""

    source = "metacritic"

    async def refresh(
        self,
        app_id: int,
        *,
        title: str,
        steam_url: str | None = None,
        critic_score: int | None = None,
        force: bool = False,
        client: httpx.AsyncClient | None = None,
    ) -> SourceRefresh:
        async def run(http: httpx.AsyncClient) -> SourceRefresh:
            async def load() -> object:
                data, diagnostic = await _load_metacritic(
                    http,
                    title=title,
                    steam_url=steam_url,
                    critic_score=critic_score,
                )
                if data is not None:
                    return data
                if diagnostic is None:
                    raise SourceLoadError(
                        "failed",
                        "empty_result",
                        "Metacritic returned no data",
                    )
                raise SourceLoadError("failed", str(diagnostic.code), diagnostic.message)

            return await self._refresh_scope(app_id, "game", load, force=force)

        if client is not None:
            return await run(client)
        timeout = httpx.Timeout(
            self.config.request_timeout_seconds,
            connect=self.config.connect_timeout_seconds,
        )
        async with httpx.AsyncClient(
            timeout=timeout,
            headers={"User-Agent": self.config.user_agent},
            follow_redirects=True,
        ) as http:
            return await run(http)


__all__ = ["MetacriticSyncService"]
