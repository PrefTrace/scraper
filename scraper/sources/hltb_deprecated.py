from __future__ import annotations

from scraper.hltb_deprecated.client import _load_hltb
from scraper.wikidata.orm import SourceRefresh

from .base import CachedSourceService, SourceLoadError


class HltbSyncService(CachedSourceService):
    """Legacy HLTB synchronizer kept outside the active pipeline."""

    source = "hltb"

    async def refresh(
        self,
        app_id: int,
        title: str,
        *,
        force: bool = False,
    ) -> SourceRefresh:
        async def load() -> object:
            data, diagnostic = await _load_hltb(title)
            if data is not None:
                return data
            if diagnostic is None:
                raise SourceLoadError("failed", "empty_result", "HLTB returned no data")
            status = "not_found" if diagnostic.code == "not_found" else "failed"
            raise SourceLoadError(status, str(diagnostic.code), diagnostic.message)

        return await self._refresh_scope(app_id, "game", load, force=force)


__all__ = ["HltbSyncService"]
