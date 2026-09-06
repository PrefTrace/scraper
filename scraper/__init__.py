"""Async game data scraper application."""

from .api import get_app_ids
from .pipeline import AppIdFileFeeder, PipelineServices, ScraperPipeline
from .sources import (
    HltbSyncService,
    MetacriticSyncService,
    PCGamingWikiSyncService,
    SteamGameSyncService,
    SteamRefreshResult,
    SteamSpySyncService,
)
from .wikidata_deprecated import (
    ScraperConfig,
    ScraperDatabase,
    WikidataGame,
    WikidataSyncService,
)

__all__ = [
    "HltbSyncService",
    "AppIdFileFeeder",
    "MetacriticSyncService",
    "PCGamingWikiSyncService",
    "ScraperConfig",
    "ScraperDatabase",
    "ScraperPipeline",
    "SteamGameSyncService",
    "SteamRefreshResult",
    "SteamSpySyncService",
    "PipelineServices",
    "WikidataGame",
    "WikidataSyncService",
    "get_app_ids",
]
