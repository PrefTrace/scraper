"""Deprecated standalone PCGamingWiki source.

The source remains importable while its Cargo client is being reworked, but it
is intentionally not part of :class:`scraper.pipeline.ScraperPipeline`.
"""

from __future__ import annotations

import httpx

from scraper.pcgamingwiki_deprecated import PCGamingWikiClient, PCGamingWikiError
from scraper.wikidata_deprecated.orm import SourceRefresh

from .base import CachedSourceService, SourceLoadError


class PCGamingWikiSyncService(CachedSourceService):
    """Legacy PCGamingWiki synchronizer kept outside the active pipeline."""

    source = "pcgamingwiki"

    async def refresh(
        self,
        app_id: int,
        *,
        title: str | None = None,
        force: bool = False,
        client: PCGamingWikiClient | httpx.AsyncClient | None = None,
    ) -> SourceRefresh:
        async def run(wiki: PCGamingWikiClient) -> SourceRefresh:
            async def load() -> object:
                try:
                    return await wiki.fetch_game(app_id, title=title)
                except PCGamingWikiError as exc:
                    status = "not_found" if "not resolved" in str(exc) else "failed"
                    raise SourceLoadError(status, type(exc).__name__, str(exc)) from exc

            return await self._refresh_scope(app_id, "game", load, force=force)

        if isinstance(client, PCGamingWikiClient):
            return await run(client)
        if client is not None:
            return await run(PCGamingWikiClient(client, config=self.config))
        timeout = httpx.Timeout(
            self.config.request_timeout_seconds,
            connect=self.config.connect_timeout_seconds,
        )
        async with httpx.AsyncClient(
            timeout=timeout,
            headers={"User-Agent": self.config.user_agent},
            follow_redirects=True,
        ) as http:
            return await run(PCGamingWikiClient(http, config=self.config))


__all__ = ["PCGamingWikiSyncService"]
