from __future__ import annotations

import asyncio
import re
from collections.abc import Sequence
from typing import Any
from urllib.parse import urlparse

import httpx

from scraper.diagnostics import Diagnostic
from scraper.hltb import fetch_hltb
from scraper.metacritic import fetch_metacritic
from scraper.models import Game, MetacriticData, ReviewCollection
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
from scraper.steam.reviews import collect_reviews, fetch_summary


class ScrapeError(RuntimeError):
    """Raised when the required Steam application payload cannot be collected."""


def extract_app_id(url: str) -> int:
    parsed = urlparse(url)
    if (
        parsed.scheme not in {"http", "https"}
        or parsed.netloc.casefold() != "store.steampowered.com"
    ):
        raise ValueError("Only Steam Store URLs are supported")
    match = re.match(r"^/app/(\d+)(?:/|$)", parsed.path)
    if not match:
        raise ValueError("Steam Store URL must contain /app/<appid>/")
    return int(match.group(1))


async def _gather_optional(
    awaitable: Any,
    *,
    source: str,
    code: str,
    message: str,
) -> tuple[Any | None, Diagnostic | None]:
    try:
        return await awaitable, None
    except Exception as exc:  # provider failures become data diagnostics
        return None, Diagnostic(
            source=source,
            code=code,
            message=message,
            details={"error": str(exc)},
        )


def _steam_metacritic(data: dict[str, Any]) -> MetacriticData | None:
    raw = data.get("metacritic")
    if not isinstance(raw, dict):
        return None
    url = raw.get("url")
    score = raw.get("score")
    if not url and score is None:
        return None
    return MetacriticData(url=url, critic_score=score, platform="pc")


async def scrape(
    url: str,
    *,
    languages: Sequence[str] | None = None,
    store_country: str | None = DEFAULT_STORE_COUNTRY,
    positive_review_count: int = 4,
    negative_review_count: int = 4,
    review_pages: int = 1,
) -> Game:
    """Collect normalized game information from a Steam Store URL."""
    app_id = extract_app_id(url)
    if positive_review_count < 0 or negative_review_count < 0:
        raise ValueError("Review counts cannot be negative")
    if review_pages < 1:
        raise ValueError("review_pages must be at least 1")
    locales = normalize_locales(languages)
    store_country = normalize_store_country(store_country)
    primary = locales[0]
    diagnostics: list[Diagnostic] = []

    timeout = httpx.Timeout(35.0, connect=15.0)
    limits = httpx.Limits(max_connections=12, max_keepalive_connections=6)
    headers = {"User-Agent": "Mozilla/5.0 (compatible; game-scraper/1.0)"}
    async with httpx.AsyncClient(
        timeout=timeout,
        limits=limits,
        headers=headers,
        follow_redirects=True,
    ) as http:
        steam = SteamClient(http)

        detail_results = await asyncio.gather(
            *(_gather_optional(
                steam.app_details(
                    app_id,
                    locale,
                    store_country=store_country,
                ),
                source="steam",
                code="localized_details_failed",
                message=f"Localized Steam details unavailable for {locale.requested}",
            ) for locale in locales)
        )
        details_by_locale: dict[str, dict[str, Any]] = {}
        detail_locale_by_name: dict[str, LocaleInfo] = {}
        for locale, (details, diagnostic) in zip(locales, detail_results, strict=True):
            if details is not None:
                details_by_locale[locale.requested] = details
                detail_locale_by_name[locale.requested] = locale
            if diagnostic is not None:
                details = {"locale": locale.requested, **diagnostic.details}
                diagnostics.append(diagnostic.model_copy(update={"details": details}))
        if not details_by_locale:
            raise ScrapeError(f"Steam app {app_id} could not be loaded")

        first_locale = next(locale for locale in locales if locale.requested in details_by_locale)
        base_details = details_by_locale[first_locale.requested]
        base = parse_app_details(
            base_details,
            detail_locale_by_name[first_locale.requested],
            store_country=store_country,
        )
        localizations = {
            locale: parse_app_details(
                details,
                detail_locale_by_name[locale],
                store_country=store_country,
            )["localized"]
            for locale, details in details_by_locale.items()
        }

        steam_meta = _steam_metacritic(base_details)
        title = localizations[first_locale.requested].name or str(app_id)

        # Everything below depends only on appdetails and can run concurrently.
        store_task = _gather_optional(
            steam.store_page(app_id, primary, store_country=store_country),
            source="steam_store_html",
            code="page_unavailable",
            message="Steam Store HTML page unavailable",
        )
        achievement_task = _gather_optional(
            steam.achievements_page(app_id, primary),
            source="steam_achievements",
            code="page_unavailable",
            message="Steam achievements page unavailable",
        )

        summary_task = _gather_optional(
            fetch_summary(steam, app_id, store_country=store_country),
            source="steam_reviews",
            code="summary_unavailable",
            message="Global Steam review summary unavailable",
        )
        locale_summary_tasks = [
            _gather_optional(
                fetch_summary(
                    steam,
                    app_id,
                    locale=locale,
                    store_country=store_country,
                ),
                source="steam_reviews",
                code="localized_summary_unavailable",
                message=f"Steam review summary unavailable for {locale.requested}",
            )
            for locale in locales
        ]
        positive_task = _gather_optional(
            collect_reviews(
                steam,
                app_id,
                review_type="positive",
                count=positive_review_count,
                pages=review_pages,
            ),
            source="steam_reviews",
            code="positive_reviews_unavailable",
            message="Positive Steam reviews unavailable",
        )
        negative_task = _gather_optional(
            collect_reviews(
                steam,
                app_id,
                review_type="negative",
                count=negative_review_count,
                pages=review_pages,
            ),
            source="steam_reviews",
            code="negative_reviews_unavailable",
            message="Negative Steam reviews unavailable",
        )

        hltb_task = _gather_optional(
            fetch_hltb(title),
            source="hltb",
            code="provider_failed",
            message="HowLongToBeat provider failed",
        )
        metacritic_url = str(steam_meta.url) if steam_meta and steam_meta.url else None
        metacritic_score = steam_meta.critic_score if steam_meta else None
        metacritic_task = _gather_optional(
            fetch_metacritic(
                http,
                title=title,
                steam_url=metacritic_url,
                critic_score=metacritic_score,
            ),
            source="metacritic",
            code="provider_failed",
            message="Metacritic provider failed",
        )

        gathered_results = await asyncio.gather(
            store_task,
            achievement_task,
            summary_task,
            *locale_summary_tasks,
            positive_task,
            negative_task,
            hltb_task,
            metacritic_task,
        )
        store_result = gathered_results[0]
        achievement_result = gathered_results[1]
        summary_result = gathered_results[2]
        locale_results = gathered_results[3 : 3 + len(locales)]
        positive_result = gathered_results[3 + len(locales)]
        negative_result = gathered_results[4 + len(locales)]
        hltb_provider_result = gathered_results[5 + len(locales)]
        metacritic_provider_result = gathered_results[6 + len(locales)]

        store_html, store_diag = store_result
        if store_diag:
            diagnostics.append(store_diag)
        achievement_html, achievement_diag = achievement_result
        if achievement_diag:
            diagnostics.append(achievement_diag)

        if isinstance(store_html, str):
            table_languages = parse_language_table(store_html)
            if table_languages:
                base["supported_languages"] = table_languages
            base["tags"] = parse_tags(store_html)

        summary, summary_diag = summary_result
        if summary_diag:
            diagnostics.append(summary_diag)
        ratings_by_locale = {}
        for locale, (rating, rating_diag) in zip(locales, locale_results, strict=True):
            if rating is not None:
                ratings_by_locale[locale.requested] = rating
            if rating_diag:
                details = {"locale": locale.requested, **rating_diag.details}
                diagnostics.append(rating_diag.model_copy(update={"details": details}))
        positive_reviews, positive_diag = positive_result
        negative_reviews, negative_diag = negative_result
        if positive_diag:
            diagnostics.append(positive_diag)
        if negative_diag:
            diagnostics.append(negative_diag)

        reviews = ReviewCollection(
            positive=positive_reviews or [],
            negative=negative_reviews or [],
            pages_requested=review_pages,
            positive_requested=positive_review_count,
            negative_requested=negative_review_count,
        )
        if len(reviews.positive) < positive_review_count:
            diagnostics.append(Diagnostic(
                source="steam_reviews",
                code="insufficient_positive_reviews",
                message="Steam returned fewer positive reviews than requested",
            ))
        if len(reviews.negative) < negative_review_count:
            diagnostics.append(Diagnostic(
                source="steam_reviews",
                code="insufficient_negative_reviews",
                message="Steam returned fewer negative reviews than requested",
            ))

        achievements = (
            parse_achievements(achievement_html)
            if isinstance(achievement_html, str)
            else []
        )
        hltb_provider, hltb_outer_diag = hltb_provider_result
        metacritic_provider, metacritic_outer_diag = metacritic_provider_result
        hltb, hltb_diag = hltb_provider if hltb_provider is not None else (None, None)
        metacritic, metacritic_diag = (
            metacritic_provider if metacritic_provider is not None else (None, None)
        )
        if hltb_outer_diag:
            diagnostics.append(hltb_outer_diag)
        if metacritic_outer_diag:
            diagnostics.append(metacritic_outer_diag)
        if hltb_diag:
            diagnostics.append(hltb_diag)
        if metacritic_diag:
            diagnostics.append(metacritic_diag)
        if metacritic is None:
            metacritic = steam_meta

        return Game(
            app_id=app_id,
            store_url=f"https://store.steampowered.com/app/{app_id}/",
            store_country=store_country,
            localizations=localizations,
            type=base["type"],
            developers=base["developers"],
            publishers=base["publishers"],
            release_date=base["release_date"],
            release_date_raw=base["release_date_raw"],
            coming_soon=base["coming_soon"],
            screenshots=base["screenshots"],
            videos=base["videos"],
            header_image=base["header_image"],
            website=base["website"],
            requirements=base["requirements"],
            supported_languages=base["supported_languages"],
            platforms=base["platforms"],
            categories=base["categories"],
            genres=base["genres"],
            tags=base.get("tags", []),
            age_ratings=base["age_ratings"],
            achievements=achievements,
            achievements_url=f"https://steamcommunity.com/stats/{app_id}/achievements/",
            steam_rating=summary,
            ratings_by_locale=ratings_by_locale,
            reviews=reviews,
            metacritic=metacritic,
            how_long_to_beat=hltb,
            diagnostics=diagnostics,
        )
