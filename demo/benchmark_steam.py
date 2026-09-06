"""Run and measure the active Steam pipeline without deprecated source queues."""

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
from scraper.steam.benchmark_controls import run_positive_controls  # noqa: E402
from scraper.steam.locales import STEAM_LANGUAGE_CODE_TO_BCP47  # noqa: E402
from scraper.steam.tz_contract import STEAM_TZ_CONTRACT, audit_steam_tz_schema  # noqa: E402
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


def _schema_audit(connection: sqlite3.Connection) -> dict[str, Any]:
    """Audit the physical database against the independent TZ contract."""

    audit = audit_steam_tz_schema(connection)
    literal_errors: list[str] = []
    literal_values: dict[str, dict[str, list[str]]] = {}
    tables = {
        row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")
    }
    for table, contract in STEAM_TZ_CONTRACT.items():
        if table not in tables:
            continue
        table_values: dict[str, list[str]] = {}
        for column, allowed in contract.literals.items():
            values = [
                str(row[0])
                for row in connection.execute(
                    f'SELECT DISTINCT "{column}" FROM "{table}" '
                    f'WHERE "{column}" IS NOT NULL'
                )
            ]
            table_values[column] = sorted(values)
            invalid = sorted(set(values) - set(allowed))
            if invalid:
                literal_errors.append(f"{table}.{column}: invalid literals {invalid}")
        if table_values:
            literal_values[table] = table_values
    counts = {
        table: int(connection.execute(f'SELECT COUNT(*) FROM "{table}"').fetchone()[0])
        for table in STEAM_TABLES
        if table in tables
    }
    audit["literal_constraints"] = {
        "ok": not literal_errors,
        "errors": literal_errors,
        "values": literal_values,
    }
    audit["row_counts"] = {table: counts.get(table, 0) for table in STEAM_TABLES}
    audit["errors"].extend(literal_errors)
    audit["ok"] = bool(audit["ok"] and not literal_errors)
    return audit


def _unlink_if_possible(path: Path) -> bool:
    try:
        path.unlink()
        return True
    except (FileNotFoundError, PermissionError):
        return False


def _fresh_database_path(output_dir: Path) -> Path:
    """Return an unused SQLite path instead of silently reusing a locked DB."""

    primary = (output_dir / "pipeline.sqlite3").resolve()
    if not primary.exists() or _unlink_if_possible(primary):
        for suffix in ("-wal", "-shm"):
            _unlink_if_possible(Path(f"{primary}{suffix}"))
        return primary
    return (output_dir / f"pipeline_{time.time_ns()}.sqlite3").resolve()


def _source_coverage(connection: sqlite3.Connection) -> dict[str, Any]:
    """Measure semantic coverage in the produced database, not just row count."""

    edition_price_rows = int(
        connection.execute("SELECT COUNT(*) FROM steam_edition_prices").fetchone()[0]
    )
    bundle_price_rows = int(
        connection.execute("SELECT COUNT(*) FROM steam_bundle_prices").fetchone()[0]
    )
    total_price_rows = edition_price_rows + bundle_price_rows
    known_edition_prices = int(
        connection.execute(
            "SELECT COUNT(*) FROM steam_edition_prices WHERE TRIM(price_region) <> ''"
        ).fetchone()[0]
    )
    known_bundle_prices = int(
        connection.execute(
            "SELECT COUNT(*) FROM steam_bundle_prices WHERE TRIM(price_region) <> ''"
        ).fetchone()[0]
    )
    known_price_rows = known_edition_prices + known_bundle_prices
    allowed_languages = set(STEAM_LANGUAGE_CODE_TO_BCP47.values())
    stored_languages = [
        str(row[0])
        for row in connection.execute(
            "SELECT DISTINCT language FROM steam_supported_languages"
        )
        if row[0] is not None
    ]
    invalid_languages = sorted(set(stored_languages) - allowed_languages)
    bundle_distribution = {
        str(row[0]): int(row[1])
        for row in connection.execute(
            "SELECT bundle_id, COUNT(*) FROM steam_bundle_editions "
            "GROUP BY bundle_id ORDER BY bundle_id"
        )
    }
    orphan_bundle_editions = int(
        connection.execute(
            "SELECT COUNT(*) FROM steam_bundle_editions AS be "
            "LEFT JOIN steam_editions AS e ON e.package_id = be.package_id "
            "WHERE e.package_id IS NULL"
        ).fetchone()[0]
    )
    app_types = {
        str(row[0]): int(row[1])
        for row in connection.execute(
            "SELECT COALESCE(type, '<null>'), COUNT(*) FROM steam_apps GROUP BY type"
        )
    }
    release_status = {
        str(row[0]): int(row[1])
        for row in connection.execute(
            "SELECT COALESCE(release_status, '<null>'), COUNT(*) "
            "FROM steam_apps GROUP BY release_status"
        )
    }
    media_types = {
        str(row[0]): int(row[1])
        for row in connection.execute(
            "SELECT media_type, COUNT(*) FROM steam_media GROUP BY media_type"
        )
    }
    achievement_rows = int(
        connection.execute("SELECT COUNT(*) FROM steam_achievements").fetchone()[0]
    )
    localization_rows = int(
        connection.execute(
            "SELECT COUNT(*) FROM steam_achievement_localizations"
        ).fetchone()[0]
    )
    achievement_scopes = {
        str(row[0]): int(row[1])
        for row in connection.execute(
            "SELECT status, COUNT(*) FROM source_refreshes "
            "WHERE source = 'steam' AND scope LIKE 'achievements:%' GROUP BY status"
        )
    }
    achievement_diagnostics = [
        str(row[0])
        for row in connection.execute(
            "SELECT DISTINCT code FROM source_diagnostics "
            "WHERE source = 'steam' AND scope LIKE 'achievements:%'"
        )
    ]
    allowed_types = {"game", "application", "dlc", "soundtrack"}
    allowed_release_states = {
        "not_released",
        "advanced_access",
        "early_access",
        "released",
        "removed",
        "<null>",
    }
    core_ok = (
        (total_price_rows == 0 or known_price_rows > 0)
        and not invalid_languages
        and orphan_bundle_editions == 0
        and set(app_types).issubset(allowed_types | {"<null>"})
        and set(release_status).issubset(allowed_release_states)
    )
    achievements_status = (
        "covered"
        if achievement_rows > 0 and localization_rows > 0
        else "blocked_by_source"
        if "steam_api_key_missing" in achievement_diagnostics
        else "not_covered"
    )
    return {
        "ok": bool(core_ok and achievements_status == "covered"),
        "known_price_region": known_price_rows,
        "total_price_rows": total_price_rows,
        "supported_language_invariant": {
            "ok": not invalid_languages,
            "stored": stored_languages,
            "invalid": invalid_languages,
        },
        "bundle_membership_distribution": bundle_distribution,
        "orphan_bundle_edition_rows": orphan_bundle_editions,
        "app_type_distribution": app_types,
        "release_status_distribution": release_status,
        "media_type_distribution": media_types,
        "achievements": {
            "rows": achievement_rows,
            "localization_rows": localization_rows,
            "refresh_statuses": achievement_scopes,
            "diagnostic_codes": sorted(achievement_diagnostics),
            "status": achievements_status,
        },
        "core_semantics_ok": core_ok,
    }


async def run(
    input_path: Path = PROJECT_ROOT / "appids.txt",
    output_dir: Path = PROJECT_ROOT / "outputs" / "real_pipeline_10",
    *,
    limit: int = 10,
) -> dict[str, Any]:
    if limit < 1:
        raise ValueError("limit must be positive")
    await asyncio.to_thread(output_dir.mkdir, parents=True, exist_ok=True)
    database_path = await asyncio.to_thread(_fresh_database_path, output_dir)
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
    positive_controls = run_positive_controls()

    with sqlite3.connect(database_path) as connection:
        connection.execute("PRAGMA foreign_keys=ON")
        audit = _schema_audit(connection)
        integrity = connection.execute("PRAGMA integrity_check").fetchone()[0]
        foreign_key_errors = [list(row) for row in connection.execute("PRAGMA foreign_key_check")]
        coverage = _source_coverage(connection)

    schema_contract_ok = bool(audit["ok"])
    parser_semantics_ok = bool(positive_controls["ok"])
    persistence_ok = integrity == "ok" and not foreign_key_errors
    benchmark_control_cases_ok = bool(
        positive_controls["ok"] and coverage["core_semantics_ok"]
    )
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
        "sqlite_foreign_key_errors": foreign_key_errors,
        "schema_audit": audit,
        "schema_contract_ok": schema_contract_ok,
        "parser_semantics_ok": parser_semantics_ok,
        "persistence_ok": persistence_ok,
        "benchmark_control_cases_ok": benchmark_control_cases_ok,
        "source_coverage_ok": bool(coverage["ok"]),
        "positive_controls": positive_controls,
        "source_coverage": coverage,
        "coverage_before_after": {
            "scope": "Steam only; Wikidata deprecated and skipped",
            "before": {table: 0 for table in STEAM_TABLES},
            "after": audit["row_counts"],
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
                "source_coverage": coverage,
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    bundle_distribution_text = json.dumps(
        coverage["bundle_membership_distribution"], ensure_ascii=False, sort_keys=True
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
        f"- schema_contract_ok: `{schema_contract_ok}`",
        f"- parser_semantics_ok: `{parser_semantics_ok}`",
        f"- persistence_ok: `{persistence_ok}`",
        f"- benchmark_control_cases_ok: `{benchmark_control_cases_ok}`",
        "",
        "## Coverage state",
        "",
        f"- Price region: `{coverage['known_price_region']}/{coverage['total_price_rows']}` "
        "rows have authoritative Steam currency.",
        f"- Achievements: `{coverage['achievements']['status']}` "
        f"({coverage['achievements']['rows']} base rows, "
        f"{coverage['achievements']['localization_rows']} localization rows).",
        f"- Supported-language invariant: `{coverage['supported_language_invariant']['ok']}`.",
        f"- Orphan bundle editions: `{coverage['orphan_bundle_edition_rows']}`.",
        "- Bundle membership distribution: "
        f"`{bundle_distribution_text}`.",
        "",
        "## Positive controls",
        "",
        f"- Passed: `{positive_controls['passed']}/{positive_controls['total']}`.",
    ]
    lines.extend(["", "## Steam table rows", "", "| Table | Rows |", "|---|---:|"])
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
