"""Run the active Steam pipeline without deprecated source queues."""

from __future__ import annotations

import argparse
import asyncio
import json
import sqlite3
import sys
import time
from collections import Counter
from dataclasses import replace
from pathlib import Path
from typing import Any, cast

import httpx

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from scraper.pipeline import AppIdFileFeeder, PipelineServices, ScraperPipeline  # noqa: E402
from scraper.sources.steam import SteamGameSyncService  # noqa: E402
from scraper.wikidata_deprecated import ScraperConfig, ScraperDatabase  # noqa: E402

STEAM_TABLES = (
    "steam_apps",
    "steam_app_localizations",
    "steam_media",
    "steam_app_editions",
    "steam_editions",
    "steam_edition_prices",
    "steam_bundles",
    "steam_bundle_editions",
    "steam_bundle_prices",
    "steam_external_links",
    "steam_age_ratings",
    "steam_descriptors",
    "steam_system_requirements",
    "steam_features",
    "steam_accessibility_features",
    "steam_deck_support",
    "steam_eulas",
    "steam_controllers",
    "steam_organization_credits",
    "steam_supported_languages",
    "steam_build_branches",
    "steam_review_language_stats",
    "steam_reviews",
    "steam_external_reviews",
    "steam_achievements",
    "steam_achievement_localizations",
)


class HttpMetrics:
    def __init__(self) -> None:
        self.requests = 0
        self.successful = 0
        self.http_errors = 0
        self.exceptions = 0
        self.durations: list[float] = []

    def add(self, duration: float, status: int | None, failed: bool) -> None:
        self.requests += 1
        self.durations.append(duration)
        if failed:
            self.exceptions += 1
        elif status is not None and status >= 400:
            self.http_errors += 1
        else:
            self.successful += 1

    def report(self) -> dict[str, Any]:
        ordered = sorted(self.durations)
        p95 = (
            ordered[min(len(ordered) - 1, max(0, int(len(ordered) * 0.95) - 1))]
            if ordered
            else None
        )
        return {
            "requests": self.requests,
            "successful": self.successful,
            "http_errors": self.http_errors,
            "exceptions": self.exceptions,
            "total_seconds": round(sum(self.durations), 4),
            "avg_seconds": round(sum(self.durations) / len(self.durations), 4)
            if self.durations
            else None,
            "p95_seconds": round(p95, 4) if p95 is not None else None,
        }


def _columns(connection: sqlite3.Connection, table: str) -> list[str]:
    return [row[1] for row in connection.execute(f'PRAGMA table_info("{table}")')]


def _schema_audit(connection: sqlite3.Connection) -> dict[str, Any]:
    tables = {
        row[0]
        for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")
    }
    expected: dict[str, set[str]] = {
        "steam_apps": {
            "app_id",
            "type",
            "demo_id",
            "dlc_for_app_id",
            "optional_dlc",
            "required_app_id",
            "linux_build",
            "windows_build",
            "mac_build",
            "vac_enabled",
            "metacritic_name",
            "metacritic_score",
            "metacritic_url",
            "gamepad_preferred",
            "controller_support",
            "release_date",
            "release_date_max",
            "release_status",
            "external_account_notice",
            "drm_notice",
        },
        "steam_app_localizations": {
            "app_id",
            "language",
            "name",
            "short_description",
            "about",
            "long_description",
            "legal_notice",
        },
        "steam_media": {"id", "app_id", "media_type", "url", "format", "language"},
        "steam_app_editions": {"app_id", "package_id"},
        "steam_editions": {"package_id", "name", "description"},
        "steam_edition_prices": {
            "package_id",
            "price_region",
            "initial",
            "final",
            "discount_percent",
            "price_type",
            "period",
            "period_units",
        },
        "steam_bundles": {"bundle_id", "name", "discount_percent", "must_purchase_as_set"},
        "steam_bundle_editions": {"bundle_id", "package_id"},
        "steam_bundle_prices": {
            "bundle_id",
            "price_region",
            "effective_discount_percent",
            "initial",
            "final",
        },
        "steam_external_links": {"id", "app_id", "type", "url", "value"},
        "steam_age_ratings": {
            "age_id",
            "app_id",
            "standard",
            "rating_generated",
            "use_age_gate",
            "banned",
            "rating",
            "minimum_age",
            "descriptor_raw",
        },
        "steam_descriptors": {"id", "age_id", "steam_id", "name"},
        "steam_system_requirements": {"id", "app_id", "platform", "level", "html"},
        "steam_features": {"app_id", "category_id", "english_name"},
        "steam_accessibility_features": {"app_id", "category_id", "english_name"},
        "steam_deck_support": {"app_id", "status"},
        "steam_eulas": {
            "id",
            "app_id",
            "eula_id",
            "name_description",
            "steam_link_support",
            "url",
            "version",
        },
        "steam_controllers": {"app_id", "controller", "bluetooth", "usb"},
        "steam_organization_credits": {"id", "app_id", "status", "organization_name"},
        "steam_supported_languages": {"app_id", "language", "audio", "text", "subtitles"},
        "steam_build_branches": {
            "app_id",
            "name",
            "updated_at",
            "description",
            "build_id",
            "download_size",
            "disk_size",
        },
        "steam_review_language_stats": {
            "id",
            "app_id",
            "language",
            "total_reviews",
            "total_negative",
            "total_positive",
            "review_score",
        },
        "steam_reviews": {
            "id",
            "app_id",
            "recommendation_id",
            "user_id",
            "playtime_forever",
            "playtime_last_two_weeks",
            "playtime_at_review",
            "deck_playtime_at_review",
            "datetime_last_played",
            "datetime_created",
            "datetime_updated",
            "datetime_dev_responded",
            "votes_up",
            "votes_funny",
            "weighted_vote_score",
            "comment_count",
            "steam_purchase",
            "received_for_free",
            "written_during_early_access",
            "primarily_steam_deck",
            "voted_up",
            "language",
            "review_text",
            "developer_response",
        },
        "steam_external_reviews": {"id", "app_id", "organization", "rating", "url", "quote"},
        "steam_achievements": {
            "id",
            "app_id",
            "api_name",
            "achievement_id",
            "icon_url",
            "global_percent",
            "hidden",
        },
        "steam_achievement_localizations": {
            "id",
            "achievement_id",
            "language",
            "name",
            "description",
        },
    }
    errors: list[str] = []
    for table, expected_columns in expected.items():
        actual = set(_columns(connection, table)) if table in tables else set()
        if table not in tables:
            errors.append(f"missing table {table}")
        if actual != expected_columns:
            errors.append(f"{table}: columns {sorted(actual)} != {sorted(expected_columns)}")
    deprecated_tables = sorted(
        table
        for table in tables
        if table.startswith("wikidata_") or table in {"source_facts"}
    )
    if deprecated_tables:
        errors.append(f"deprecated tables present: {deprecated_tables}")
    fk_rows = connection.execute('PRAGMA foreign_key_list("steam_descriptors")').fetchall()
    if not any(row[2] == "steam_age_ratings" and row[4] == "age_id" for row in fk_rows):
        errors.append("steam_descriptors.age_id has no FK to steam_age_ratings.age_id")
    counts = {
        table: int(connection.execute(f'SELECT COUNT(*) FROM "{table}"').fetchone()[0])
        for table in STEAM_TABLES
    }
    return {
        "ok": not errors,
        "errors": errors,
        "tables": sorted(tables),
        "row_counts": counts,
    }


def _unlink_if_possible(path: Path) -> None:
    try:
        path.unlink()
    except (FileNotFoundError, PermissionError):
        pass


async def run(
    input_path: Path = PROJECT_ROOT / "appids.txt",
    output_dir: Path = PROJECT_ROOT / "outputs" / "real_pipeline_10",
    *,
    limit: int = 10,
) -> dict[str, Any]:
    if limit < 1:
        raise ValueError("limit must be positive")
    await asyncio.to_thread(output_dir.mkdir, parents=True, exist_ok=True)
    database_path = await asyncio.to_thread(lambda: (output_dir / "pipeline.sqlite3").resolve())
    for path in (database_path, Path(f"{database_path}-wal"), Path(f"{database_path}-shm")):
        await asyncio.to_thread(_unlink_if_possible, path)
    input_path = await asyncio.to_thread(input_path.resolve)
    app_ids = AppIdFileFeeder(input_path).read(limit=limit)
    if len(app_ids) != limit:
        raise ValueError(f"Expected {limit} AppIDs, found {len(app_ids)}")

    config = replace(
        ScraperConfig.from_env(),
        database_url=f"sqlite+aiosqlite:///{database_path.as_posix()}",
        app_ids_file=str(input_path),
    )
    database = ScraperDatabase(config)
    pipeline = ScraperPipeline(
        PipelineServices(steam=SteamGameSyncService(database), wikidata=None)
    )
    metrics = HttpMetrics()
    original_get: Any = httpx.AsyncClient.get

    async def measured_get(
        client: httpx.AsyncClient, url: Any, *args: Any, **kwargs: Any
    ) -> httpx.Response:
        started = time.perf_counter()
        try:
            response = await original_get(client, url, *args, **kwargs)
        except Exception:
            metrics.add(time.perf_counter() - started, None, True)
            raise
        metrics.add(time.perf_counter() - started, response.status_code, False)
        return cast(httpx.Response, response)

    setattr(httpx.AsyncClient, "get", measured_get)  # noqa: B010
    started = time.perf_counter()
    started_at_utc = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    try:
        added = await pipeline.run_from_file(str(input_path), limit=limit)
        states = await pipeline.queue.states()
    finally:
        setattr(httpx.AsyncClient, "get", original_get)  # noqa: B010
        await database.dispose()
    elapsed = time.perf_counter() - started

    with sqlite3.connect(database_path) as connection:
        connection.execute("PRAGMA foreign_keys=ON")
        audit = _schema_audit(connection)
        integrity = connection.execute("PRAGMA integrity_check").fetchone()[0]

    queue_statuses = Counter(state.status for state in states)
    benchmark: dict[str, Any] = {
        "source": "Steam",
        "deprecated_sources_skipped": ["Wikidata"],
        "started_at_utc": started_at_utc,
        "input_file": str(input_path),
        "limit": limit,
        "app_ids": app_ids,
        "database_file": str(database_path),
        "added_primary_tasks": added,
        "elapsed_seconds": round(elapsed, 4),
        "queue": {"total": len(states), "statuses": dict(queue_statuses)},
        "http_metrics": metrics.report(),
        "sqlite_integrity": integrity,
        "schema_audit": audit,
        "known_source_limits": {
            "organization_credits": (
                "Public Steam data exposes organization names but no stable organization ID."
            ),
            "achievements": (
                "Public endpoints returned localized names without stable achievement IDs/API "
                "names; "
                "display names are not used as identity."
            ),
        },
    }
    (output_dir / "pipeline_benchmark.json").write_text(
        json.dumps(benchmark, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    (output_dir / "coverage_before_after.json").write_text(
        json.dumps(
            {
                "scope": "Steam only; Wikidata deprecated and skipped",
                "before": {table: 0 for table in STEAM_TABLES},
                "after": audit["row_counts"],
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    lines = [
        "# Steam pipeline benchmark",
        "",
        "Wikidata is deprecated and was not started in this run.",
        "",
        f"- AppIDs: {', '.join(str(app_id) for app_id in app_ids)}",
        f"- Elapsed: {benchmark['elapsed_seconds']} s",
        f"- HTTP requests: {metrics.requests} ({metrics.successful} successful)",
        f"- SQLite integrity: `{integrity}`",
        f"- Schema audit: `{ 'ok' if audit['ok'] else 'failed' }`",
        "",
        "## Known source limits",
        "",
        "- Organization credits expose names but no stable public organization ID; "
        "the table stores `organization_name`.",
        "- Achievements with only localized display names are skipped because a display "
        "name is not a stable Steam identity.",
        "",
        "## Steam table rows",
        "",
        "| Table | Rows |",
        "|---|---:|",
    ]
    lines.extend(f"| `{table}` | {audit['row_counts'][table]} |" for table in STEAM_TABLES)
    if audit["errors"]:
        lines.extend(["", "## Schema errors", ""])
        lines.extend(f"- {error}" for error in audit["errors"])
    (output_dir / "pipeline_report.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    return benchmark


def main() -> None:
    parser = argparse.ArgumentParser(description="Run and measure the Steam-only pipeline")
    parser.add_argument("--input", type=Path, default=PROJECT_ROOT / "appids.txt")
    parser.add_argument(
        "--output-dir", type=Path, default=PROJECT_ROOT / "outputs" / "real_pipeline_10"
    )
    parser.add_argument("--limit", type=int, default=10)
    args = parser.parse_args()
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    print(
        json.dumps(
            asyncio.run(run(args.input, args.output_dir, limit=args.limit)),
            ensure_ascii=False,
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
