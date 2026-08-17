from __future__ import annotations

import re
from typing import Any

from howlongtobeatpy import HowLongToBeat  # type: ignore[import-untyped]

from scraper.diagnostics import Diagnostic
from scraper.models import HltbData


def _as_float(value: Any) -> float | None:
    try:
        return float(value) if value is not None else None
    except (TypeError, ValueError):
        return None


async def fetch_hltb(game_name: str) -> tuple[HltbData | None, Diagnostic | None]:
    """Find the best HLTB result for a Steam title using the library's async API."""
    normalized_name = re.sub(r"[™®�]", "", game_name)
    normalized_name = re.sub(r"\s+", " ", normalized_name).strip()
    editionless_name = re.sub(
        r"\s+(?:complete|definitive|deluxe|ultimate|gold|game of the year|goty)"
        r"(?:\s+edition)?$",
        "",
        normalized_name,
        flags=re.IGNORECASE,
    ).strip()
    search_names = list(dict.fromkeys([game_name, normalized_name, editionless_name]))
    results = []
    try:
        # Keep all results so we can apply our own conservative match policy.
        searcher = HowLongToBeat(
            input_minimum_similarity=0.0,
            input_auto_filter_times=False,
        )
        for search_name in search_names:
            found = await searcher.async_search(search_name, similarity_case_sensitive=False)
            if found:
                results.extend(found)
                if any(str(item.game_name).casefold() == search_name.casefold() for item in found):
                    break
    except Exception as exc:  # the provider is explicitly best-effort
        return None, Diagnostic(
            source="hltb",
            code="request_failed",
            message="HowLongToBeat request failed",
            details={"error": str(exc)},
        )

    if not results:
        return None, Diagnostic(
            source="hltb",
            code="not_found",
            message=f"No HowLongToBeat match found for {game_name!r}",
        )

    exact = [
        item
        for item in results
        if str(item.game_name).casefold() in {name.casefold() for name in search_names}
    ]
    candidates = exact or sorted(results, key=lambda item: item.similarity, reverse=True)
    best = candidates[0]
    similarity = 1.0 if exact else float(best.similarity)
    if similarity < 0.65:
        return None, Diagnostic(
            source="hltb",
            code="ambiguous_match",
            message=f"No high-confidence HowLongToBeat match found for {game_name!r}",
            details={
                "best_match": best.game_name,
                "similarity": similarity,
                "candidates": [item.game_name for item in candidates[:5]],
            },
        )

    return HltbData(
        id=best.game_id,
        name=str(best.game_name),
        url=best.game_web_link,
        main_story_hours=_as_float(best.main_story),
        main_extra_hours=_as_float(best.main_extra),
        completionist_hours=_as_float(best.completionist),
        all_styles_hours=_as_float(best.all_styles),
        raw=best.json_content if isinstance(best.json_content, dict) else {},
    ), None
