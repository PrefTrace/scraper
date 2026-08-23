from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Sequence
from dataclasses import dataclass

import httpx
from sqlalchemy import select

from scraper.steam.client import SteamClient
from scraper.steam.locales import (
    DEFAULT_STORE_COUNTRY,
    LocaleInfo,
    normalize_locales,
    normalize_store_country,
)
from scraper.steam.parsers import (
    parse_achievements,
    parse_app_details,
    parse_language_table,
    parse_tags,
)
from scraper.steam.reviews import _collect_reviews, _fetch_summary
from scraper.wikidata.orm import SourceFact, SourceRefresh

from .base import CachedSourceService


@dataclass(slots=True)
class SteamRefreshResult:
    """Current Steam refresh states plus people named by Steam."""

    refreshes: list[SourceRefresh]
    developers: list[str]
    publishers: list[str]


class SteamGameSyncService(CachedSourceService):
    """Refresh Steam game substructures into scalar ORM facts.

    The Steam AppID catalog is intentionally not part of this service. The
    existing ``fetch_app_ids`` flow remains the source of catalog IDs.
    """

    source = "steam"

    async def refresh(
        self,
        app_id: int,
        *,
        languages: Sequence[str] | None = None,
        store_country: str | None = DEFAULT_STORE_COUNTRY,
        positive_review_count: int = 4,
        negative_review_count: int = 4,
        review_pages: int = 1,
        force: bool = False,
        client: httpx.AsyncClient | None = None,
    ) -> SteamRefreshResult:
        if app_id < 0:
            raise ValueError("Steam AppID cannot be negative")
        if positive_review_count < 0 or negative_review_count < 0:
            raise ValueError("Review counts cannot be negative")
        if review_pages < 1:
            raise ValueError("review_pages must be at least 1")
        locales = normalize_locales(languages)
        country = normalize_store_country(store_country)

        async def run(http: httpx.AsyncClient) -> SteamRefreshResult:
            steam = SteamClient(http)
            operations: list[Awaitable[SourceRefresh]] = []
            detail_scopes: list[str] = []
            for locale in locales:
                locale_scope = locale.requested.replace(":", "_")
                country_scope = country or "none"
                details_scope = f"details:{locale_scope}:{country_scope}"
                detail_scopes.append(details_scope)
                store_scope = f"store:{locale_scope}:{country_scope}"
                achievement_scope = f"achievements:{locale_scope}"

                async def details_loader(locale: LocaleInfo = locale) -> object:
                    data = await steam.app_details(
                        app_id,
                        locale,
                        store_country=country,
                    )
                    return parse_app_details(data, locale, store_country=country)

                async def store_loader(locale: LocaleInfo = locale) -> object:
                    html = await steam.store_page(
                        app_id,
                        locale,
                        store_country=country,
                    )
                    return {
                        "supported_languages": parse_language_table(html),
                        "tags": parse_tags(html),
                    }

                async def achievement_loader(locale: LocaleInfo = locale) -> object:
                    html = await steam.achievements_page(app_id, locale)
                    return {"achievements": parse_achievements(html)}

                operations.extend(
                    (
                        self._refresh_scope(
                            app_id,
                            details_scope,
                            details_loader,
                            force=force,
                        ),
                        self._refresh_scope(
                            app_id,
                            store_scope,
                            store_loader,
                            force=force,
                        ),
                        self._refresh_scope(
                            app_id,
                            achievement_scope,
                            achievement_loader,
                            force=force,
                        ),
                    )
                )

                async def rating_loader(locale: LocaleInfo = locale) -> object:
                    return await _fetch_summary(
                        steam,
                        app_id,
                        locale=locale,
                        store_country=country,
                    )

                operations.append(
                    self._refresh_scope(
                        app_id,
                        f"rating:{locale_scope}:{country_scope}",
                        rating_loader,
                        force=force,
                    )
                )

            async def global_rating_loader() -> object:
                return await _fetch_summary(
                    steam,
                    app_id,
                    store_country=country,
                )

            operations.append(
                self._refresh_scope(
                    app_id,
                    f"rating:all:{country or 'none'}",
                    global_rating_loader,
                    force=force,
                )
            )

            async def positive_loader() -> object:
                return await _collect_reviews(
                    steam,
                    app_id,
                    review_type="positive",
                    count=positive_review_count,
                    pages=review_pages,
                )

            async def negative_loader() -> object:
                return await _collect_reviews(
                    steam,
                    app_id,
                    review_type="negative",
                    count=negative_review_count,
                    pages=review_pages,
                )

            operations.extend(
                (
                    self._refresh_scope(
                        app_id,
                        f"reviews:positive:{positive_review_count}:{review_pages}",
                        positive_loader,
                        force=force,
                    ),
                    self._refresh_scope(
                        app_id,
                        f"reviews:negative:{negative_review_count}:{review_pages}",
                        negative_loader,
                        force=force,
                    ),
                )
            )
            refreshes = list(await asyncio.gather(*operations))
            developers, publishers = await self._read_people(app_id, detail_scopes)
            return SteamRefreshResult(
                refreshes=refreshes,
                developers=developers,
                publishers=publishers,
            )

        if client is not None:
            return await run(client)
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
            headers={"User-Agent": self.config.user_agent},
            follow_redirects=True,
        ) as http:
            return await run(http)

    async def _read_people(
        self,
        app_id: int,
        detail_scopes: Sequence[str],
    ) -> tuple[list[str], list[str]]:
        """Read the current developer/publisher facts after all scopes finish."""

        async with self.database.session() as session:
            facts = (
                await session.scalars(
                    select(SourceFact).where(
                        SourceFact.source == self.source,
                        SourceFact.steam_app_id == app_id,
                        SourceFact.scope.in_(detail_scopes),
                    )
                )
            ).all()

        developers: list[str] = []
        publishers: list[str] = []
        for fact in facts:
            if fact.value_type != "text" or not fact.value_text:
                continue
            if fact.path.startswith("developers["):
                _append_unique(developers, fact.value_text)
            elif fact.path.startswith("publishers["):
                _append_unique(publishers, fact.value_text)
        return developers, publishers


def _append_unique(values: list[str], value: str) -> None:
    if value not in values:
        values.append(value)


__all__ = ["SteamGameSyncService", "SteamRefreshResult"]
