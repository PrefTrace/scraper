from __future__ import annotations

import json
import re
import unicodedata
from typing import Any

import httpx
from selectolax.parser import HTMLParser

from scraper.diagnostics import Diagnostic
from scraper.models import MetacriticData


def _node_text(node: Any) -> str:
    return re.sub(r"\s+", " ", node.text(separator=" ", strip=True)).strip() if node else ""


def _number(value: str | None, *, maximum: float) -> float | None:
    if not value:
        return None
    match = re.search(r"\d+(?:\.\d+)?", value)
    if not match:
        return None
    number = float(match.group(0))
    return number if 0 <= number <= maximum else None


def _slugify_title(title: str) -> str:
    title = re.sub(r"[™®©]", "", title)
    ascii_title = unicodedata.normalize("NFKD", title).encode("ascii", "ignore").decode("ascii")
    return re.sub(r"[^a-zA-Z0-9]+", "-", ascii_title).strip("-").lower()


def _metacritic_urls(title: str, steam_url: str | None) -> list[str]:
    urls: list[str] = []
    if steam_url:
        urls.append(steam_url)

    normalized_title = re.sub(r"\s+", " ", title).strip()
    editionless_title = re.sub(
        r"\s+(?:complete|definitive|deluxe|ultimate|gold|game of the year|goty)"
        r"(?:\s+edition)?$",
        "",
        normalized_title,
        flags=re.IGNORECASE,
    ).strip()
    for candidate_title in dict.fromkeys((normalized_title, editionless_title)):
        slug = _slugify_title(candidate_title)
        if slug:
            urls.extend(
                (
                    f"https://www.metacritic.com/game/pc/{slug}/",
                    f"https://www.metacritic.com/game/{slug}/",
                )
            )
    return list(dict.fromkeys(urls))


def parse_metacritic_html(html: str, url: str) -> MetacriticData:
    parser = HTMLParser(html)
    values = [
        _node_text(node)
        for node in parser.css('span[data-testid="global-score-value"]')
    ]
    headers = [
        _node_text(node)
        for node in parser.css('div[data-testid="global-score-header"]')
    ]

    critic_score: int | None = None
    user_score: float | None = None
    user_score_raw: str | None = None
    for header, value in zip(headers, values, strict=False):
        if header.casefold() == "metascore":
            parsed = _number(value, maximum=100)
            critic_score = int(parsed) if parsed is not None else None
        elif header.casefold() == "user score":
            user_score_raw = value
            user_score = _number(value, maximum=10)

    # JSON-LD is a stable fallback for critic score and canonical URL.
    if critic_score is None:
        for node in parser.css('script[type="application/ld+json"]'):
            try:
                payload = json.loads(node.text())
            except (TypeError, ValueError, json.JSONDecodeError):
                continue
            if not isinstance(payload, dict):
                continue
            aggregate = payload.get("aggregateRating")
            if isinstance(aggregate, dict):
                parsed = _number(str(aggregate.get("ratingValue", "")), maximum=100)
                if parsed is not None:
                    critic_score = int(parsed)
                    break

    return MetacriticData(
        url=url,
        critic_score=critic_score,
        user_score=user_score,
        user_score_raw=user_score_raw,
        platform="pc",
    )


async def fetch_metacritic(
    client: httpx.AsyncClient,
    *,
    title: str,
    steam_url: str | None = None,
    critic_score: int | None = None,
) -> tuple[MetacriticData | None, Diagnostic | None]:
    """Fetch Metacritic data using Steam's URL and a title-based PC fallback."""
    candidate_urls = _metacritic_urls(title, steam_url)
    if not candidate_urls:
        return None, Diagnostic(
            source="metacritic",
            code="url_unavailable",
            message=f"Could not build a Metacritic URL for {title!r}",
        )

    errors: list[str] = []
    for candidate_url in candidate_urls:
        try:
            response = await client.get(
                candidate_url,
                follow_redirects=True,
                headers={"User-Agent": "Mozilla/5.0 (compatible; game-scraper/1.0)"},
            )
            response.raise_for_status()
            data = parse_metacritic_html(response.text, str(response.url))
            if data.critic_score is None and critic_score is not None:
                data.critic_score = critic_score
            if (
                steam_url == candidate_url
                or data.critic_score is not None
                or data.user_score is not None
            ):
                return data, None
            errors.append(f"No score found at {candidate_url}")
        except (httpx.HTTPError, ValueError) as exc:
            errors.append(f"{candidate_url}: {exc}")

    return None, Diagnostic(
        source="metacritic",
        code="request_failed",
        message=f"Metacritic lookup failed for {title!r}",
        details={"candidates": candidate_urls, "errors": errors},
    )
