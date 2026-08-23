from __future__ import annotations

import argparse
import asyncio
import json
import sys
import time
from dataclasses import replace
from pathlib import Path

import httpx
from sqlalchemy import func, select

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from scraper.wikidata import (  # noqa: E402
    ScraperConfig,
    ScraperDatabase,
    WikidataEntity,
    WikidataFact,
    WikidataGameLink,
    WikidataSyncService,
)

DEFAULT_APP_IDS = [620, 1_091_500]
DEFAULT_OUTPUT_DIR = Path(__file__).resolve().parent


async def _count_rows(database: ScraperDatabase, model: type[object]) -> int:
    async with database.session() as session:
        return int((await session.scalar(select(func.count()).select_from(model))) or 0)


async def run(app_ids: list[int], output_dir: Path) -> dict[str, object]:
    await asyncio.to_thread(output_dir.mkdir, parents=True, exist_ok=True)
    database_path = (output_dir / "wikidata.sqlite3").resolve()
    config = replace(
        ScraperConfig.from_env(),
        database_url=f"sqlite+aiosqlite:///{database_path.as_posix()}",
    )
    database = ScraperDatabase(config)
    service = WikidataSyncService(database)
    await service.ensure_schema()
    results: list[dict[str, object]] = []

    try:
        for app_id in app_ids:
            request_count = 0

            async def count_request(_request: httpx.Request) -> None:
                nonlocal request_count
                request_count += 1

            started = time.perf_counter()
            error: str | None = None
            async with httpx.AsyncClient(
                timeout=httpx.Timeout(
                    config.request_timeout_seconds,
                    connect=config.connect_timeout_seconds,
                ),
                limits=httpx.Limits(
                    max_connections=config.concurrency,
                    max_keepalive_connections=max(2, config.concurrency // 2),
                ),
                headers={
                    "User-Agent": config.user_agent,
                    "Accept-Language": "en,ru;q=0.8",
                },
                follow_redirects=True,
                event_hooks={"request": [count_request]},
            ) as http:
                try:
                    game = await service.refresh_game_task(app_id, client=http)
                except Exception as exc:  # pragma: no cover - demo diagnostics
                    game = None
                    error = f"{type(exc).__name__}: {exc}"
            elapsed = time.perf_counter() - started

            results.append(
                {
                    "app_id": app_id,
                    "elapsed_seconds": round(elapsed, 3),
                    "http_requests": request_count,
                    "found": game is not None and game.status == "ready",
                    "wikidata_item": game.item_qid if game is not None else None,
                    "status": game.status if game is not None else "failed",
                    "error": error,
                    "entities_in_db": await _count_rows(database, WikidataEntity),
                    "facts_in_db": await _count_rows(database, WikidataFact),
                    "game_links_in_db": await _count_rows(database, WikidataGameLink),
                }
            )
    finally:
        await database.dispose()

    benchmark = {
        "source": "Wikidata Query Service + Wikibase API",
        "storage": "SQLite ORM",
        "lookup_property": "P1733",
        "app_ids": app_ids,
        "database_file": database_path.name,
        "results": results,
        "total_elapsed_seconds": round(
            sum(
                float(result["elapsed_seconds"])
                for result in results
                if isinstance(result["elapsed_seconds"], (int, float))
            ),
            3,
        ),
    }
    await asyncio.to_thread(
        (output_dir / "wikidata_benchmark.json").write_text,
        json.dumps(benchmark, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return benchmark


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Refresh Wikidata into the ORM and measure timings"
    )
    parser.add_argument("--app-id", type=int, action="append", dest="app_ids")
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    args = parser.parse_args()
    benchmark = asyncio.run(run(args.app_ids or DEFAULT_APP_IDS, args.output_dir))
    print(json.dumps(benchmark, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
