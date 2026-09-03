from __future__ import annotations

import httpx

from scraper.steamspy_deprecated import SteamSpyClient, SteamSpyError
from scraper.wikidata.orm import SourceRefresh

from .base import CachedSourceService, SourceLoadError


class SteamSpySyncService(CachedSourceService):
    """Legacy SteamSpy synchronizer kept outside the active pipeline."""

    source = "steamspy"

    async def refresh(
        self,
        app_id: int,
        *,
        force: bool = False,
        client: SteamSpyClient | httpx.AsyncClient | None = None,
    ) -> SourceRefresh:
        async def run(steamspy: SteamSpyClient) -> SourceRefresh:
            async def load() -> object:
                try:
                    return await steamspy.fetch_stats(app_id)
                except SteamSpyError as exc:
                    status = "not_found" if "hidden" in str(exc) else "failed"
                    raise SourceLoadError(status, type(exc).__name__, str(exc)) from exc

            return await self._refresh_scope(app_id, "stats", load, force=force)

        if isinstance(client, SteamSpyClient):
            return await run(client)
        if client is not None:
            return await run(SteamSpyClient(client, config=self.config))
        timeout = httpx.Timeout(
            self.config.request_timeout_seconds,
            connect=self.config.connect_timeout_seconds,
        )
        async with httpx.AsyncClient(
            timeout=timeout,
            headers={"User-Agent": self.config.user_agent},
            follow_redirects=True,
        ) as http:
            return await run(SteamSpyClient(http, config=self.config))


__all__ = ["SteamSpySyncService"]
