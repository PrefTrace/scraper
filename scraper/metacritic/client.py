from __future__ import annotations

import json
import re
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
    """Fetch Metacritic data from the canonical Steam-provided URL when available."""
    if not steam_url:
        return None, Diagnostic(
            source="metacritic",
            code="url_unavailable",
            message=f"Steam did not provide a Metacritic URL for {title!r}",
        )
    try:
        response = await client.get(
            steam_url,
            follow_redirects=True,
            headers={"User-Agent": "Mozilla/5.0 (compatible; game-scraper/1.0)"},
        )
        response.raise_for_status()
        data = parse_metacritic_html(response.text, str(response.url))
        if data.critic_score is None and critic_score is not None:
            data.critic_score = critic_score
        return data, None
    except (httpx.HTTPError, ValueError) as exc:
        return None, Diagnostic(
            source="metacritic",
            code="request_failed",
            message=f"Metacritic request failed for {title!r}",
            details={"error": str(exc)},
        )

