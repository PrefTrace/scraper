from __future__ import annotations

import re
import unicodedata
from datetime import UTC, datetime
from typing import Any

from scraper.models import RatingSummary, Review, ReviewAuthor

from .client import SteamClient
from .locales import LocaleInfo

_REVIEW_LANGUAGES = {
    "english": "en",
    "russian": "ru",
    "german": "de",
    "french": "fr",
    "spanish": "es",
    "brazilian": "pt-BR",
    "portuguese": "pt",
    "schinese": "zh-CN",
    "tchinese": "zh-TW",
    "japanese": "ja",
    "koreana": "ko",
    "polish": "pl",
    "italian": "it",
    "dutch": "nl",
    "danish": "da",
    "finnish": "fi",
    "swedish": "sv",
    "norwegian": "no",
    "czech": "cs",
    "hungarian": "hu",
    "turkish": "tr",
    "ukrainian": "uk",
}


def _dt(value: Any) -> datetime | None:
    try:
        return datetime.fromtimestamp(int(value), tz=UTC) if value else None
    except (TypeError, ValueError, OSError):
        return None


def _percent(positive: int, negative: int) -> float | None:
    total = positive + negative
    return round(positive * 100 / total, 2) if total else None


def _review_language(value: Any) -> str | None:
    if not isinstance(value, str) or not value.strip():
        return None
    normalized = value.strip()
    if normalized.casefold() == "all":
        return None
    return _REVIEW_LANGUAGES.get(normalized.casefold(), normalized)


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
        review_language=_review_language(review_language),
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
        deck_playtime_at_review_minutes=author_data.get("deck_playtime_at_review"),
        deck_playtime_at_review=author_data.get("deck_playtime_at_review"),
        last_played=_dt(author_data.get("last_played")),
    )
    steam_id = author.steam_id
    source_url = (
        f"https://steamcommunity.com/profiles/{steam_id}/recommended/{app_id}/"
        if steam_id
        else f"https://steamcommunity.com/app/{app_id}/reviews/"
    )
    return _annotate_review(
        Review(
            recommendation_id=str(payload.get("recommendationid", "")),
            text=str(payload.get("review", "")),
            positive=bool(payload.get("voted_up")),
            voted_up=payload.get("voted_up"),
            source_url=source_url,
            language=_review_language(payload.get("language")),
            created_at=_dt(payload.get("timestamp_created")),
            updated_at=_dt(payload.get("timestamp_updated")),
            developer_responded_at=_dt(
                payload.get("timestamp_dev_responded")
                or payload.get("developer_response_timestamp")
            ),
            votes_up=int(payload.get("votes_up", 0) or 0),
            votes_funny=int(payload.get("votes_funny", 0) or 0),
            weighted_vote_score=float(payload["weighted_vote_score"])
            if payload.get("weighted_vote_score") not in (None, "")
            else None,
            comment_count=int(payload.get("comment_count", 0) or 0),
            steam_purchase=payload.get("steam_purchase"),
            received_for_free=payload.get("received_for_free"),
            written_during_early_access=payload.get("written_during_early_access"),
            primarily_steam_deck=payload.get("primarily_steam_deck"),
            deck_playtime_at_review_minutes=payload.get("deck_playtime_at_review"),
            deck_playtime_at_review=payload.get("deck_playtime_at_review"),
            developer_response=payload.get("developer_response"),
            author=author,
        )
    )


async def _collect_reviews(
    client: SteamClient,
    app_id: int,
    *,
    review_type: str,
    count: int,
    pages: int | None,
    minimum_text_length: int = 200,
) -> list[Review]:
    if count <= 0 or pages == 0:
        return []
    collected: list[Review] = []
    cursor = "*"
    page_count = 0
    exhausted = False
    while pages is None or page_count < pages:
        page_count += 1
        payload = await client.review_page(
            app_id,
            language="all",
            review_type=review_type,
            cursor=cursor,
        )
        raw_reviews = payload.get("reviews") or []
        collected.extend(
            parse_review(item, app_id) for item in raw_reviews if isinstance(item, dict)
        )
        selected = _select_reviews(
            collected,
            count=count,
            minimum_text_length=minimum_text_length,
        )
        if len(selected) >= count:
            return selected
        next_cursor = payload.get("cursor")
        if not raw_reviews or not next_cursor or next_cursor == cursor:
            exhausted = True
            break
        cursor = str(next_cursor)

    # If all available pages were exhausted and there are not enough long
    # reviews, relax only the length requirement. Deduplication and the
    # text-art filter remain mandatory.
    if not exhausted:
        return _select_reviews(
            collected,
            count=count,
            minimum_text_length=minimum_text_length,
        )
    return _select_reviews(
        collected,
        count=count,
        minimum_text_length=None,
        relaxed_from=minimum_text_length,
    )


def _annotate_review(review: Review) -> Review:
    _, length, word_count, alpha_ratio = _review_metrics(review.text)
    review.text_length_chars = length
    review.word_count = word_count
    review.alpha_ratio = alpha_ratio
    return review


def _review_metrics(text: str) -> tuple[str, int, int, float]:
    normalized = unicodedata.normalize("NFKC", text)
    normalized = re.sub(r"\s+", " ", normalized).strip()
    non_whitespace = [character for character in normalized if not character.isspace()]
    alphanumeric = sum(character.isalnum() for character in non_whitespace)
    alpha_ratio = alphanumeric / len(non_whitespace) if non_whitespace else 0.0
    words = re.findall(r"[^\W_]+(?:['’\-][^\W_]+)*", normalized, flags=re.UNICODE)
    return normalized, len(normalized), len(words), alpha_ratio


def _deduplication_key(review: Review) -> str:
    normalized, _, _, _ = _review_metrics(review.text)
    return normalized.casefold()


def _is_ascii_art(review: Review) -> bool:
    if not review.text.strip() or review.alpha_ratio == 0.0:
        return True
    if review.alpha_ratio < 0.35 and review.word_count < 20:
        return True

    lines = [line.strip() for line in review.text.splitlines() if line.strip()]
    if len(lines) < 4:
        return False
    decorative_lines = sum(not any(character.isalnum() for character in line) for line in lines)
    return decorative_lines / len(lines) >= 0.5 and review.word_count < 20


def _review_sort_key(review: Review) -> tuple[int, float, int, int, datetime]:
    return (
        review.text_length_chars,
        review.weighted_vote_score or 0.0,
        review.votes_up,
        review.comment_count,
        review.created_at or datetime.min.replace(tzinfo=UTC),
    )


def _select_reviews(
    candidates: list[Review],
    *,
    count: int,
    minimum_text_length: int | None,
    relaxed_from: int | None = None,
) -> list[Review]:
    selected: list[Review] = []
    seen_texts: set[str] = set()
    seen_authors: set[str] = set()
    for review in sorted(candidates, key=_review_sort_key, reverse=True):
        _annotate_review(review)
        review.quality_status = "rejected"
        review.quality_reason = None
        review.selection_rank = None

        if _is_ascii_art(review):
            review.quality_reason = "ascii_art"
            continue
        deduplication_key = _deduplication_key(review)
        if deduplication_key in seen_texts:
            review.quality_reason = "duplicate_text"
            continue
        if minimum_text_length is not None and review.text_length_chars < minimum_text_length:
            review.quality_reason = "too_short"
            continue
        author_id = review.author.steam_id if review.author is not None else None
        if author_id is not None and author_id in seen_authors:
            review.quality_reason = "duplicate_author"
            continue
        seen_texts.add(deduplication_key)
        if author_id is not None:
            seen_authors.add(author_id)
        review.quality_status = "accepted"
        review.quality_reason = (
            "minimum_length_relaxed"
            if relaxed_from is not None and review.text_length_chars < relaxed_from
            else None
        )
        review.selection_rank = len(selected) + 1
        selected.append(review)
        if len(selected) >= count:
            break
    return selected


async def _fetch_summary(
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
