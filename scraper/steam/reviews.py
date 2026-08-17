from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from scraper.models import RatingSummary, Review, ReviewAuthor

from .client import SteamClient
from .locales import LocaleInfo


def _dt(value: Any) -> datetime | None:
    try:
        return datetime.fromtimestamp(int(value), tz=UTC) if value else None
    except (TypeError, ValueError, OSError):
        return None


def _percent(positive: int, negative: int) -> float | None:
    total = positive + negative
    return round(positive * 100 / total, 2) if total else None


def parse_summary(
    query_summary: dict[str, Any] | None,
    *,
    locale: str | None = None,
    review_language: str | None = None,
    store_country: str | None = None,
) -> RatingSummary:
    summary = query_summary or {}
    positive = int(summary.get("total_positive", 0) or 0)
    negative = int(summary.get("total_negative", 0) or 0)
    return RatingSummary(
        locale=locale,
        review_language=review_language,
        store_country=store_country,
        score=summary.get("review_score"),
        score_description=summary.get("review_score_desc"),
        total_positive=positive,
        total_negative=negative,
        total_reviews=int(summary.get("total_reviews", positive + negative) or 0),
        positive_percent=_percent(positive, negative),
    )


def parse_review(payload: dict[str, Any], app_id: int) -> Review:
    author_data = payload.get("author") or {}
    author = ReviewAuthor(
        steam_id=str(author_data["steamid"]) if author_data.get("steamid") else None,
        games_owned=author_data.get("num_games_owned"),
        reviews_written=author_data.get("num_reviews"),
        playtime_forever_minutes=author_data.get("playtime_forever"),
        playtime_last_two_weeks_minutes=author_data.get("playtime_last_two_weeks"),
        playtime_at_review_minutes=author_data.get("playtime_at_review"),
        last_played=_dt(author_data.get("last_played")),
    )
    steam_id = author.steam_id
    source_url = (
        f"https://steamcommunity.com/profiles/{steam_id}/recommended/{app_id}/"
        if steam_id
        else f"https://steamcommunity.com/app/{app_id}/reviews/"
    )
    return Review(
        recommendation_id=str(payload.get("recommendationid", "")),
        text=str(payload.get("review", "")),
        positive=bool(payload.get("voted_up")),
        source_url=source_url,
        language=payload.get("language"),
        created_at=_dt(payload.get("timestamp_created")),
        updated_at=_dt(payload.get("timestamp_updated")),
        votes_up=int(payload.get("votes_up", 0) or 0),
        votes_funny=int(payload.get("votes_funny", 0) or 0),
        weighted_vote_score=float(payload["weighted_vote_score"])
        if payload.get("weighted_vote_score") not in (None, "")
        else None,
        comment_count=int(payload.get("comment_count", 0) or 0),
        steam_purchase=payload.get("steam_purchase"),
        received_for_free=payload.get("received_for_free"),
        written_during_early_access=payload.get("written_during_early_access"),
        developer_response=payload.get("developer_response"),
        author=author,
    )


async def collect_reviews(
    client: SteamClient,
    app_id: int,
    *,
    review_type: str,
    count: int,
    pages: int,
) -> list[Review]:
    if count <= 0 or pages <= 0:
        return []
    collected: list[Review] = []
    cursor = "*"
    for _ in range(pages):
        payload = await client.review_page(
            app_id,
            language="all",
            review_type=review_type,
            cursor=cursor,
        )
        raw_reviews = payload.get("reviews") or []
        collected.extend(
            parse_review(item, app_id)
            for item in raw_reviews
            if isinstance(item, dict)
        )
        next_cursor = payload.get("cursor")
        if not raw_reviews or not next_cursor or next_cursor == cursor:
            break
        cursor = str(next_cursor)

    # The API order is Steam's helpfulness order. The requested policy is to
    # choose the most detailed texts within that bounded Steam-ranked window.
    collected.sort(
        key=lambda item: (
            len(item.text.strip()),
            item.weighted_vote_score or 0.0,
            item.votes_up,
            item.created_at or datetime.min.replace(tzinfo=UTC),
        ),
        reverse=True,
    )
    return collected[:count]


async def fetch_summary(
    client: SteamClient,
    app_id: int,
    *,
    locale: LocaleInfo | None = None,
    store_country: str | None = None,
) -> RatingSummary:
    language = locale.steam_language if locale else "all"
    payload = await client.review_page(
        app_id,
        language=language,
        review_type="all",
        cursor="*",
    )
    return parse_summary(
        payload.get("query_summary"),
        locale=locale.requested if locale else None,
        review_language=language,
        store_country=store_country,
    )
