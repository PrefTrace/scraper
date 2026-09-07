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
from scraper.sources.steam import SteamGameSyncService, SteamRefreshResult  # noqa: E402
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
    "steam_category_localizations",
    "steam_deck_support",
    "steam_eulas",
    "steam_controllers",
    "steam_organization_credits",
    "steam_organizations",
    "steam_supported_languages",
    "steam_depots",
    "steam_app_depots",
    "steam_depot_os",
    "steam_depot_manifests",
    "steam_tags",
    "steam_tag_localizations",
    "steam_genres",
    "steam_genre_localizations",
    "steam_workshop_stats",
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
                    f'SELECT DISTINCT "{column}" FROM "{table}" WHERE "{column}" IS NOT NULL'
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


def _boolean_distribution(
    connection: sqlite3.Connection, table: str, column: str
) -> dict[str, int]:
    return {
        str(row[0]): int(row[1])
        for row in connection.execute(
            f"SELECT CASE WHEN \"{column}\" IS NULL THEN '<null>' "
            f"WHEN \"{column}\" = 1 THEN 'true' "
            f"WHEN \"{column}\" = 0 THEN 'false' "
            f'ELSE CAST("{column}" AS TEXT) END, COUNT(*) '
            f'FROM "{table}" GROUP BY 1 ORDER BY 1'
        )
    }


def _empty_string_violations(connection: sqlite3.Connection) -> dict[str, int]:
    violations: dict[str, int] = {}
    for table in STEAM_TABLES:
        table_info = connection.execute(f'PRAGMA table_info("{table}")').fetchall()
        for _cid, column, declared_type, _not_null, _default, _pk in table_info:
            declared = str(declared_type or "").upper()
            if not any(token in declared for token in ("TEXT", "CHAR", "CLOB")):
                continue
            count = int(
                connection.execute(
                    f'SELECT COUNT(*) FROM "{table}" '
                    f'WHERE "{column}" IS NOT NULL '
                    f"AND TRIM(CAST(\"{column}\" AS TEXT)) = ''"
                ).fetchone()[0]
            )
            if count:
                violations[f"{table}.{column}"] = count
    return violations


def _source_coverage(connection: sqlite3.Connection) -> dict[str, Any]:
    """Measure semantic coverage in the produced database, not just row count."""

    edition_price_rows = int(
        connection.execute("SELECT COUNT(*) FROM steam_edition_prices").fetchone()[0]
    )
    bundle_price_rows = int(
        connection.execute("SELECT COUNT(*) FROM steam_bundle_prices").fetchone()[0]
    )
    total_price_rows = edition_price_rows + bundle_price_rows
    price_observation_states = {
        "total": edition_price_rows,
        "observed_unavailable": int(
            connection.execute(
                "SELECT COUNT(*) FROM steam_edition_prices WHERE initial IS NULL AND final IS NULL"
            ).fetchone()[0]
        ),
        "permanent_free": int(
            connection.execute(
                "SELECT COUNT(*) FROM steam_edition_prices "
                "WHERE initial = 0 AND final = 0 AND discount_percent IS NULL"
            ).fetchone()[0]
        ),
        "free_promotion_candidates": int(
            connection.execute(
                "SELECT COUNT(*) FROM steam_edition_prices WHERE initial > 0 AND final = 0"
            ).fetchone()[0]
        ),
        "paid_available": int(
            connection.execute(
                "SELECT COUNT(*) FROM steam_edition_prices WHERE final > 0"
            ).fetchone()[0]
        ),
        "anomaly_initial_null_final_present": int(
            connection.execute(
                "SELECT COUNT(*) FROM steam_edition_prices "
                "WHERE initial IS NULL AND final IS NOT NULL"
            ).fetchone()[0]
        ),
    }
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
    known_currency_rows = int(
        connection.execute(
            "SELECT COUNT(*) FROM ("
            "SELECT currency FROM steam_edition_prices UNION ALL "
            "SELECT currency FROM steam_bundle_prices) "
            "WHERE currency IS NOT NULL AND TRIM(currency) <> ''"
        ).fetchone()[0]
    )
    price_regions = {
        str(row[0]): int(row[1])
        for row in connection.execute(
            "SELECT price_region, COUNT(*) FROM ("
            "SELECT price_region FROM steam_edition_prices UNION ALL "
            "SELECT price_region FROM steam_bundle_prices) GROUP BY price_region"
        )
        if row[0] is not None
    }
    currencies = {
        str(row[0]): int(row[1])
        for row in connection.execute(
            "SELECT currency, COUNT(*) FROM ("
            "SELECT currency FROM steam_edition_prices UNION ALL "
            "SELECT currency FROM steam_bundle_prices) GROUP BY currency"
        )
        if row[0] is not None
    }
    price_consistency_violations = int(
        connection.execute(
            "SELECT COUNT(*) FROM ("
            "SELECT initial, final, discount_percent FROM steam_edition_prices UNION ALL "
            "SELECT initial, final, effective_discount_percent FROM steam_bundle_prices"
            ") WHERE initial IS NOT NULL AND final IS NOT NULL AND discount_percent IS NOT NULL "
            "AND initial > 0 AND ABS(discount_percent - "
            "ROUND((initial - final) * 100.0 / initial)) > 1"
        ).fetchone()[0]
    )
    active_discounts_with_end = int(
        connection.execute(
            "SELECT COUNT(*) FROM steam_edition_prices "
            "WHERE discount_percent > 0 AND discount_end_at IS NOT NULL"
        ).fetchone()[0]
    ) + int(
        connection.execute(
            "SELECT COUNT(*) FROM steam_bundle_prices "
            "WHERE effective_discount_percent > 0 AND discount_end_at IS NOT NULL"
        ).fetchone()[0]
    )
    allowed_languages = set(STEAM_LANGUAGE_CODE_TO_BCP47.values())
    stored_languages = [
        str(row[0])
        for row in connection.execute("SELECT DISTINCT language FROM steam_supported_languages")
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
        connection.execute("SELECT COUNT(*) FROM steam_achievement_localizations").fetchone()[0]
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
    empty_strings = _empty_string_violations(connection)
    boolean_distributions = {
        f"{table}.{column}": _boolean_distribution(connection, table, column)
        for table in STEAM_TABLES
        for _cid, column, declared_type, _not_null, _default, _pk in connection.execute(
            f'PRAGMA table_info("{table}")'
        ).fetchall()
        if "BOOL" in str(declared_type or "").upper()
    }
    unknown_category_rows = {
        "unknown_ids": sorted(
            {
                int(row[0])
                for table in ("steam_features", "steam_accessibility_features")
                for row in connection.execute(
                    f"SELECT relation.category_id FROM {table} AS relation "
                    "WHERE NOT EXISTS (SELECT 1 FROM steam_category_localizations AS localization "
                    "WHERE localization.category_id = relation.category_id)"
                )
            }
        ),
        "feature_without_localization": int(
            connection.execute(
                "SELECT COUNT(*) FROM steam_features AS relation "
                "WHERE NOT EXISTS (SELECT 1 FROM steam_category_localizations AS localization "
                "WHERE localization.category_id = relation.category_id)"
            ).fetchone()[0]
        ),
        "accessibility_without_localization": int(
            connection.execute(
                "SELECT COUNT(*) FROM steam_accessibility_features AS relation "
                "WHERE NOT EXISTS (SELECT 1 FROM steam_category_localizations AS localization "
                "WHERE localization.category_id = relation.category_id)"
            ).fetchone()[0]
        ),
        "localization_count": int(
            connection.execute("SELECT COUNT(*) FROM steam_category_localizations").fetchone()[0]
        ),
        "diagnostics": int(
            connection.execute(
                "SELECT COUNT(*) FROM source_diagnostics "
                "WHERE source = 'steam' AND code = 'unknown_steam_category'"
            ).fetchone()[0]
        ),
    }
    descriptor_metrics = {
        "raw_rows": int(
            connection.execute(
                "SELECT COUNT(*) FROM steam_age_ratings "
                "WHERE descriptor_raw IS NOT NULL AND TRIM(descriptor_raw) <> ''"
            ).fetchone()[0]
        ),
        "normalized_rows": int(
            connection.execute(
                "SELECT COUNT(*) FROM steam_descriptors WHERE name IS NOT NULL AND TRIM(name) <> ''"
            ).fetchone()[0]
        ),
        "unresolved_rows": int(
            connection.execute(
                "SELECT COUNT(*) FROM steam_descriptors WHERE name IS NULL"
            ).fetchone()[0]
        ),
        "rejected_rows": 0,
    }
    branch_count = int(
        connection.execute("SELECT COUNT(*) FROM steam_build_branches").fetchone()[0]
    )
    branch_profile_count = int(
        connection.execute(
            "SELECT COUNT(*) FROM steam_build_branches WHERE "
            "download_size_min IS NOT NULL OR disk_size_min IS NOT NULL"
        ).fetchone()[0]
    )
    controller_rows = int(
        connection.execute("SELECT COUNT(*) FROM steam_controllers").fetchone()[0]
    )
    controller_types = {
        str(row[0]): int(row[1])
        for row in connection.execute(
            "SELECT controller, COUNT(*) FROM steam_controllers "
            "GROUP BY controller ORDER BY controller"
        )
    }
    controller_transport = {
        column: _boolean_distribution(connection, "steam_controllers", column)
        for column in ("bluetooth", "usb")
    }
    generic_external_links = int(
        connection.execute(
            "SELECT COUNT(*) FROM steam_external_links WHERE LOWER(type) = 'external'"
        ).fetchone()[0]
    )
    supported_language_flags = {
        column: _boolean_distribution(connection, "steam_supported_languages", column)
        for column in ("audio", "text", "subtitles")
    }
    unresolved_editions = int(
        connection.execute(
            "SELECT COUNT(*) FROM steam_editions WHERE name IS NULL AND description IS NULL"
        ).fetchone()[0]
    )
    review_language_nulls = int(
        connection.execute(
            "SELECT COUNT(*) FROM steam_review_language_stats WHERE language IS NULL"
        ).fetchone()[0]
    )
    rating_rows = connection.execute(
        "SELECT standard, rating, minimum_age FROM steam_age_ratings"
    ).fetchall()
    rating_minimum_age_coverage = sum(row[2] is not None for row in rating_rows)
    rating_decodable_but_null = sum(
        row[2] is None and bool(row[1]) and str(row[1]).strip().isdigit() for row in rating_rows
    )
    depot_count = int(connection.execute("SELECT COUNT(*) FROM steam_depots").fetchone()[0])
    shared_depot_count = int(
        connection.execute(
            "SELECT COUNT(*) FROM steam_depots WHERE depot_from_app IS NOT NULL"
        ).fetchone()[0]
    )
    depot_os_count = int(connection.execute("SELECT COUNT(*) FROM steam_depot_os").fetchone()[0])
    manifest_count = int(
        connection.execute("SELECT COUNT(*) FROM steam_depot_manifests").fetchone()[0]
    )
    suspicious_zero_profiles = int(
        connection.execute(
            "SELECT COUNT(*) FROM steam_build_branches WHERE "
            "download_size_min = 0 OR download_size_median = 0 OR download_size_max = 0 "
            "OR disk_size_min = 0 OR disk_size_median = 0 OR disk_size_max = 0"
        ).fetchone()[0]
    )
    branches_without_size = int(
        connection.execute(
            "SELECT COUNT(*) FROM steam_build_branches WHERE "
            "download_size_min IS NULL AND disk_size_min IS NULL"
        ).fetchone()[0]
    )
    download_zero_profiles = int(
        connection.execute(
            "SELECT COUNT(*) FROM steam_build_branches WHERE "
            "download_size_min = 0 OR download_size_median = 0 OR download_size_max = 0"
        ).fetchone()[0]
    )
    disk_zero_profiles = int(
        connection.execute(
            "SELECT COUNT(*) FROM steam_build_branches WHERE "
            "disk_size_min = 0 OR disk_size_median = 0 OR disk_size_max = 0"
        ).fetchone()[0]
    )
    all_public_null_apps = int(
        connection.execute(
            "SELECT COUNT(*) FROM steam_apps AS app WHERE EXISTS ("
            "SELECT 1 FROM steam_build_branches branch WHERE branch.app_id=app.app_id "
            "AND branch.name='public') AND NOT EXISTS ("
            "SELECT 1 FROM steam_build_branches branch WHERE branch.app_id=app.app_id "
            "AND branch.name='public' AND (branch.download_size_min IS NOT NULL "
            "OR branch.disk_size_min IS NOT NULL))"
        ).fetchone()[0]
    )
    suspiciously_small_profiles = int(
        connection.execute(
            "SELECT COUNT(*) FROM steam_build_branches WHERE "
            "download_size_min IS NOT NULL AND download_size_min > 0 "
            "AND download_size_min < 1000000"
        ).fetchone()[0]
    )
    tag_count = int(connection.execute("SELECT COUNT(*) FROM steam_tags").fetchone()[0])
    tag_localization_count = int(
        connection.execute("SELECT COUNT(*) FROM steam_tag_localizations").fetchone()[0]
    )
    tags_with_weight = int(
        connection.execute("SELECT COUNT(*) FROM steam_tags WHERE weight IS NOT NULL").fetchone()[0]
    )
    synthetic_tag_index_rows = int(
        connection.execute(
            "SELECT COUNT(*) FROM steam_tags WHERE tag_id BETWEEN 0 AND 19"
        ).fetchone()[0]
    )
    unique_tag_ids = int(
        connection.execute("SELECT COUNT(DISTINCT tag_id) FROM steam_tags").fetchone()[0]
    )
    tags_without_english_localization = int(
        connection.execute(
            "SELECT COUNT(DISTINCT tag.tag_id) FROM steam_tags tag WHERE NOT EXISTS ("
            "SELECT 1 FROM steam_tag_localizations localization "
            "WHERE localization.tag_id=tag.tag_id AND localization.language='en')"
        ).fetchone()[0]
    )
    synthetic_tag_pattern_apps = int(
        connection.execute(
            "SELECT COUNT(*) FROM (SELECT app_id FROM steam_tags GROUP BY app_id "
            "HAVING COUNT(DISTINCT tag_id)=20 AND SUM(CASE WHEN tag_id BETWEEN 0 AND 19 "
            "THEN 1 ELSE 0 END)=20)"
        ).fetchone()[0]
    )
    genre_count = int(connection.execute("SELECT COUNT(*) FROM steam_genres").fetchone()[0])
    genre_localization_count = int(
        connection.execute("SELECT COUNT(*) FROM steam_genre_localizations").fetchone()[0]
    )
    organizations_resolved = int(
        connection.execute(
            "SELECT COUNT(*) FROM steam_organization_credits "
            "WHERE creator_clan_account_id IS NOT NULL"
        ).fetchone()[0]
    )
    organizations_unresolved = int(
        connection.execute(
            "SELECT COUNT(*) FROM steam_organization_credits WHERE creator_clan_account_id IS NULL"
        ).fetchone()[0]
    )
    organizations_total = int(
        connection.execute("SELECT COUNT(*) FROM steam_organizations").fetchone()[0]
    )
    organization_enrichment_success_rate = (
        organizations_resolved / (organizations_resolved + organizations_unresolved)
        if organizations_resolved + organizations_unresolved
        else None
    )
    workshop_available = int(
        connection.execute(
            "SELECT COUNT(*) FROM steam_workshop_stats WHERE workshop_available = 1"
        ).fetchone()[0]
    )
    workshop_item_coverage = int(
        connection.execute(
            "SELECT COUNT(*) FROM steam_workshop_stats WHERE published_file_count IS NOT NULL"
        ).fetchone()[0]
    )
    workshop_collection_coverage = int(
        connection.execute(
            "SELECT COUNT(*) FROM steam_workshop_stats WHERE collection_count IS NOT NULL"
        ).fetchone()[0]
    )
    workshop_copied_totals = int(
        connection.execute(
            "SELECT COUNT(*) FROM steam_workshop_stats "
            "WHERE published_file_count IS NOT NULL AND collection_count IS NOT NULL "
            "AND published_file_count = collection_count"
        ).fetchone()[0]
    )
    runtime_restriction_diagnostics = int(
        connection.execute(
            "SELECT COUNT(*) FROM source_diagnostics WHERE source = 'steam' "
            "AND code LIKE 'steam_runtime_restriction_%'"
        ).fetchone()[0]
    )
    runtime_diagnostic_categories = {
        str(row[0]): int(row[1])
        for row in connection.execute(
            "SELECT code, COUNT(*) FROM source_diagnostics WHERE source='steam' "
            "AND code IN ('steam_pics_source_unavailable','steam_pics_token_required',"
            "'steam_pics_fields_unavailable','steam_runtime_restriction_ambiguous',"
            "'steam_runtime_restriction_source_unavailable') GROUP BY code"
        )
    }
    regional_edition_diagnostics = int(
        connection.execute(
            "SELECT COUNT(*) FROM source_diagnostics WHERE source = 'steam' "
            "AND code LIKE 'steam_regional_edition_%'"
        ).fetchone()[0]
    )
    duplicate_diagnostics = int(
        connection.execute(
            "SELECT COUNT(*) FROM (SELECT source, steam_app_id, scope, code, COUNT(*) AS n "
            "FROM source_diagnostics GROUP BY source, steam_app_id, scope, code HAVING n > 1)"
        ).fetchone()[0]
    )
    orphan_packages = int(
        connection.execute(
            "SELECT COUNT(*) FROM steam_editions e WHERE NOT EXISTS "
            "(SELECT 1 FROM steam_app_editions ae WHERE ae.package_id=e.package_id) AND NOT EXISTS "
            "(SELECT 1 FROM steam_bundle_editions be WHERE be.package_id=e.package_id)"
        ).fetchone()[0]
    )
    orphan_bundles = int(
        connection.execute(
            "SELECT COUNT(*) FROM steam_bundles b WHERE NOT EXISTS "
            "(SELECT 1 FROM steam_bundle_editions be WHERE be.bundle_id=b.bundle_id)"
        ).fetchone()[0]
    )
    orphan_depots = int(
        connection.execute(
            "SELECT COUNT(*) FROM steam_depots depot WHERE NOT EXISTS ("
            "SELECT 1 FROM steam_app_depots relation WHERE relation.depot_id=depot.depot_id)"
        ).fetchone()[0]
    )
    duplicate_media_assets = int(
        connection.execute(
            "SELECT COUNT(*) FROM (SELECT url, COALESCE(language, '') AS language "
            "FROM steam_media GROUP BY url, COALESCE(language, '') "
            "HAVING COUNT(DISTINCT media_type) > 1)"
        ).fetchone()[0]
    )
    category_localizations_by_language = {
        str(row[0]): int(row[1])
        for row in connection.execute(
            "SELECT language, COUNT(*) FROM steam_category_localizations GROUP BY language"
        )
    }
    genre_localizations_by_language = {
        str(row[0]): int(row[1])
        for row in connection.execute(
            "SELECT language, COUNT(*) FROM steam_genre_localizations GROUP BY language"
        )
    }
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
        (total_price_rows == 0 or known_price_rows == total_price_rows)
        and not invalid_languages
        and orphan_bundle_editions == 0
        and not empty_strings
        and review_language_nulls == 0
        and set(app_types).issubset(allowed_types | {"<null>"})
        and set(release_status).issubset(allowed_release_states)
        and price_consistency_violations == 0
        and workshop_copied_totals == 0
        and duplicate_diagnostics == 0
    )
    achievements_status = (
        "covered"
        if achievement_rows > 0 and localization_rows > 0
        else "blocked_by_source"
        if "steam_api_key_missing" in achievement_diagnostics
        else "not_covered"
    )
    return {
        # Achievement API availability is reported separately below.  A
        # missing optional credential must not make the Steam DB benchmark
        # fail when all source-semantic invariants pass.
        "ok": bool(core_ok),
        "known_price_regions": known_price_rows,
        "known_currency": known_currency_rows,
        "total_price_rows": total_price_rows,
        "price_observations": price_observation_states,
        "price_regions": price_regions,
        "currencies": currencies,
        "price_consistency_violations": price_consistency_violations,
        "active_discounts_with_end_at": active_discounts_with_end,
        "empty_string_violations": empty_strings,
        "nullable_boolean_distributions": boolean_distributions,
        "categories": unknown_category_rows,
        "descriptors": descriptor_metrics,
        "unresolved_editions": unresolved_editions,
        "branch_profiles": {
            "branches": branch_count,
            "with_install_profiles": branch_profile_count,
            "coverage": branch_profile_count / branch_count if branch_count else None,
            "without_size_info": branches_without_size,
            "download_min_zero": download_zero_profiles,
            "disk_min_zero": disk_zero_profiles,
            "suspiciously_small": suspiciously_small_profiles,
            "all_public_sizes_null_apps": all_public_null_apps,
            "control_apps": {
                str(app_id): [list(row) for row in connection.execute(
                    "SELECT name, download_size_min, download_size_median, download_size_max, "
                    "disk_size_min, disk_size_median, disk_size_max "
                    "FROM steam_build_branches WHERE app_id=? AND name IN ('public','alpha4') "
                    "ORDER BY name",
                    (app_id,),
                ).fetchall()]
                for app_id in (294100, 647960, 292030)
            },
        },
        "controllers": {"supported_rows": controller_rows},
        "controller_types": controller_types,
        "controller_transport": controller_transport,
        "generic_external_links": generic_external_links,
        "supported_language_flags": supported_language_flags,
        "review_language_nulls": review_language_nulls,
        "rating_minimum_age": {
            "coverage": rating_minimum_age_coverage,
            "rows": len(rating_rows),
            "decodable_but_null": rating_decodable_but_null,
        },
        "depots": {
            "count": depot_count,
            "shared_count": shared_depot_count,
            "os_relations": depot_os_count,
            "manifest_count": manifest_count,
            "suspicious_zero_profiles": suspicious_zero_profiles,
            "orphan_count": orphan_depots,
            "unresolved_shared_count": int(
                connection.execute(
                    "SELECT COUNT(*) FROM source_diagnostics WHERE source='steam' "
                    "AND code='steam_shared_depot_unresolved'"
                ).fetchone()[0]
            ),
            "empty_or_invalid_language_count": int(
                connection.execute(
                    "SELECT COUNT(*) FROM steam_depots WHERE language IS NULL"
                ).fetchone()[0]
            ),
            "unknown_nonempty_language_diagnostics": int(
                connection.execute(
                    "SELECT COUNT(*) FROM source_diagnostics WHERE source='steam' "
                    "AND code='unknown_steam_depot_language'"
                ).fetchone()[0]
            ),
        },
        "tags": {
            "count": tag_count,
            "unique_ids": unique_tag_ids,
            "localization_count": tag_localization_count,
            "with_weight": tags_with_weight,
            "weight_null": tag_count - tags_with_weight,
            "synthetic_index_rows": synthetic_tag_index_rows,
            "ids_without_en_localization": tags_without_english_localization,
            "en_localization_coverage": (
                (unique_tag_ids - tags_without_english_localization) / unique_tag_ids
                if unique_tag_ids
                else None
            ),
            "synthetic_0_19_pattern_apps": synthetic_tag_pattern_apps,
        },
        "genres": {
            "count": genre_count,
            "localization_count": genre_localization_count,
            "localizations_by_language": genre_localizations_by_language,
        },
        "organizations": {
            "credits_total": organizations_resolved + organizations_unresolved,
            "resolved": organizations_resolved,
            "unresolved": organizations_unresolved,
            "organizations_total": organizations_total,
            "creator_enrichment_success_rate": organization_enrichment_success_rate,
        },
        "workshop": {
            "available": workshop_available,
            "item_count_coverage": workshop_item_coverage,
            "collection_count_coverage": workshop_collection_coverage,
            "copied_total_rows": workshop_copied_totals,
        },
        "category_localizations_by_language": category_localizations_by_language,
        "regional_price_semantics": {
            "regional_edition_known": int(
                connection.execute(
                    "SELECT COUNT(*) FROM steam_edition_prices WHERE regional_edition IS NOT NULL"
                ).fetchone()[0]
            ),
            "runtime_restriction_known": int(
                connection.execute(
                    "SELECT COUNT(*) FROM steam_edition_prices "
                    "WHERE run_region_restricted IS NOT NULL"
                ).fetchone()[0]
            ),
            "regional_edition_diagnostics": regional_edition_diagnostics,
            "runtime_restriction_diagnostics": runtime_restriction_diagnostics,
            "runtime_diagnostic_categories": runtime_diagnostic_categories,
        },
        "duplicate_diagnostics": duplicate_diagnostics,
        "orphan_packages": orphan_packages,
        "orphan_bundles": orphan_bundles,
        "orphan_depots": orphan_depots,
        "duplicate_media_assets": duplicate_media_assets,
        "soundtrack_catalog_coverage": {
            "status": "incomplete",
            "reason": "The active GetAppList catalog path has no include_music scope.",
        },
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


def _real_integration_controls(connection: sqlite3.Connection) -> dict[str, Any]:
    """Verify live-source facts from the produced DB, separate from fixtures."""

    cases: list[dict[str, Any]] = []

    def add(name: str, ok: bool, evidence: dict[str, Any]) -> None:
        cases.append({"name": name, "ok": ok, "evidence": evidence})

    branch_controls = {
        (294100, "alpha4"): "294100_alpha4_manifest_zero_is_unknown",
        (647960, "public"): "647960_empty_language_is_unrestricted",
        (292030, "public"): "292030_public_full_profile",
    }
    for (app_id, branch_name), case_name in branch_controls.items():
        rows = connection.execute(
            "SELECT download_size_min, download_size_median, download_size_max, "
            "disk_size_min, disk_size_median, disk_size_max FROM steam_build_branches "
            "WHERE app_id=? AND name=?",
            (app_id, branch_name),
        ).fetchall()
        row = rows[0] if rows else None
        if app_id == 294100:
            ok = bool(
                row
                and row[0] is not None
                and row[1] is not None
                and row[0] != 0
                and row[1] != 0
            )
        elif app_id == 647960:
            ok = bool(row and any(value is not None for value in row))
        else:
            ok = bool(row and row[0] is not None and row[0] >= 30_000_000_000)
        add(case_name, ok, {"rows": [list(item) for item in rows]})

    usk_rows = connection.execute(
        "SELECT rating, minimum_age FROM steam_age_ratings "
        "WHERE app_id = 281990 AND LOWER(standard) = 'usk'"
    ).fetchall()
    add(
        "281990_usk_numeric_minimum_age",
        any(str(rating).strip() == "6" and minimum_age == 6 for rating, minimum_age in usk_rows),
        {"rows": [list(row) for row in usk_rows]},
    )
    free_rows = connection.execute(
        "SELECT price_region, currency, initial, final, discount_percent "
        "FROM steam_edition_prices WHERE package_id = 320246"
    ).fetchall()
    add(
        "320246_permanent_free",
        any(
            initial == 0 and final == 0 and discount is None
            for _, _, initial, final, discount in free_rows
        ),
        {"rows": [list(row) for row in free_rows]},
    )
    active_discount_rows = connection.execute(
        "SELECT package_id, discount_type, discount_end_at FROM steam_edition_prices "
        "WHERE discount_percent > 0 AND discount_type IS NOT NULL AND discount_end_at IS NOT NULL "
        "UNION ALL SELECT bundle_id, discount_type, discount_end_at FROM steam_bundle_prices "
        "WHERE effective_discount_percent > 0 AND discount_type IS NOT NULL "
        "AND discount_end_at IS NOT NULL"
    ).fetchall()
    add(
        "active_discount_type_and_end",
        bool(active_discount_rows),
        {"rows": [list(row) for row in active_discount_rows[:10]]},
    )
    tag_rows = connection.execute(
        "SELECT tag.tag_id, tag.weight, localization.language, localization.name "
        "FROM steam_tags AS tag JOIN steam_tag_localizations AS localization "
        "ON localization.tag_id = tag.tag_id "
        "WHERE tag.tag_id NOT BETWEEN 0 AND 19 AND tag.weight IS NOT NULL"
    ).fetchall()
    add(
        "real_tag_id_weight_localization",
        bool(tag_rows),
        {"rows": [list(row) for row in tag_rows[:10]]},
    )
    tag_summary = connection.execute(
        "SELECT COUNT(*), COUNT(DISTINCT tag_id), "
        "SUM(CASE WHEN weight IS NULL THEN 1 ELSE 0 END), "
        "SUM(CASE WHEN weight IS NOT NULL THEN 1 ELSE 0 END) FROM steam_tags"
    ).fetchone()
    en_tag_coverage = connection.execute(
        "SELECT COUNT(DISTINCT tag.tag_id), COUNT(DISTINCT localization.tag_id) "
        "FROM steam_tags tag LEFT JOIN steam_tag_localizations localization "
        "ON localization.tag_id=tag.tag_id AND localization.language='en'"
    ).fetchone()
    synthetic_apps = connection.execute(
        "SELECT app_id FROM steam_tags GROUP BY app_id HAVING COUNT(DISTINCT tag_id)=20 "
        "AND SUM(CASE WHEN tag_id BETWEEN 0 AND 19 THEN 1 ELSE 0 END)=20"
    ).fetchall()
    add(
        "tag_coverage_not_synthetic",
        bool(tag_summary and tag_summary[3] == tag_summary[0])
        and bool(en_tag_coverage and en_tag_coverage[0] == en_tag_coverage[1])
        and not synthetic_apps,
        {
            "relations": tag_summary[0] if tag_summary else 0,
            "unique_ids": tag_summary[1] if tag_summary else 0,
            "weight_null": tag_summary[2] if tag_summary else 0,
            "weight_non_null": tag_summary[3] if tag_summary else 0,
            "en_ids": list(en_tag_coverage) if en_tag_coverage else [],
            "synthetic_apps": [row[0] for row in synthetic_apps],
        },
    )
    credit_rows = connection.execute(
        "SELECT app_id, status, creator_clan_account_id, credited_name "
        "FROM steam_organization_credits WHERE creator_clan_account_id IS NOT NULL"
    ).fetchall()
    add(
        "organization_creator_clan_id",
        bool(credit_rows),
        {"rows": [list(row) for row in credit_rows[:10]]},
    )
    workshop_copies = int(
        connection.execute(
            "SELECT COUNT(*) FROM steam_workshop_stats "
            "WHERE published_file_count IS NOT NULL AND collection_count IS NOT NULL "
            "AND published_file_count = collection_count"
        ).fetchone()[0]
    )
    add("workshop_collection_not_copied", workshop_copies == 0, {"copied_rows": workshop_copies})
    no_price_rows = connection.execute(
        "SELECT be.bundle_id, be.package_id, p.price_region, p.initial, p.final "
        "FROM steam_bundle_editions be JOIN steam_edition_prices p "
        "ON p.package_id=be.package_id AND p.price_region='KZ' "
        "WHERE p.initial IS NULL AND p.final IS NULL LIMIT 10"
    ).fetchall()
    add(
        "real_bundle_package_unavailable_price_observation",
        bool(no_price_rows),
        {"rows": [list(row) for row in no_price_rows]},
    )
    depot_languages = [
        str(row[0])
        for row in connection.execute(
            "SELECT DISTINCT language FROM steam_depots "
            "WHERE language IS NOT NULL ORDER BY language"
        )
    ]
    add(
        "multilingual_depot_bcp47",
        len(depot_languages) >= 2,
        {"languages": depot_languages},
    )
    regional_rows = connection.execute(
        "SELECT package_id, price_region, regional_edition FROM steam_edition_prices"
    ).fetchall()
    regional_diagnostics = int(
        connection.execute(
            "SELECT COUNT(*) FROM source_diagnostics WHERE source = 'steam' "
            "AND code LIKE 'steam_regional_edition_%'"
        ).fetchone()[0]
    )
    add(
        "regional_edition_candidate",
        bool(regional_rows)
        and (any(row[2] is not None for row in regional_rows) or regional_diagnostics > 0),
        {"rows": [list(row) for row in regional_rows[:10]], "diagnostics": regional_diagnostics},
    )
    runtime_rows = connection.execute(
        "SELECT package_id, price_region, run_region_restricted FROM steam_edition_prices"
    ).fetchall()
    runtime_diagnostics = int(
        connection.execute(
            "SELECT COUNT(*) FROM source_diagnostics WHERE source = 'steam' "
            "AND code LIKE 'steam_runtime_restriction_%'"
        ).fetchone()[0]
    )
    add(
        "runtime_region_restriction_result_or_diagnostic",
        bool(runtime_rows)
        and (any(row[2] is not None for row in runtime_rows) or runtime_diagnostics > 0),
        {"rows": [list(row) for row in runtime_rows[:10]], "diagnostics": runtime_diagnostics},
    )
    return {
        "ok": all(case["ok"] for case in cases),
        "total": len(cases),
        "passed": sum(case["ok"] for case in cases),
        "failed": [case for case in cases if not case["ok"]],
        "cases": cases,
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
    steam_service = SteamGameSyncService(database)
    # Benchmark price rows are an explicit KZ observation, not a global map.
    pipeline = ScraperPipeline(
        PipelineServices(steam=steam_service, wikidata=None, steam_store_country="kz")
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
    idempotency_before: dict[str, int] = {}
    idempotency_results: list[Any] = []
    idempotency_elapsed: float | None = None
    try:
        added = await pipeline.run_from_file(str(input_path), limit=limit)
        states = await pipeline.queue.states()
        with sqlite3.connect(database_path) as connection:
            idempotency_before = _schema_audit(connection)["row_counts"]
        idempotency_started = time.perf_counter()
        # A forced repeat is intentionally serialized.  The first pass is
        # concurrent and measures normal throughput; bursting the same ten
        # apps immediately again makes Steam answer with rate limits rather
        # than measuring persistence idempotency.
        idempotency_results = []
        for app_id in app_ids:
            try:
                idempotency_results.append(
                    await steam_service.refresh(app_id, store_country="kz", force=True)
                )
            except Exception as exc:  # keep the benchmark report complete
                idempotency_results.append(exc)
        idempotency_elapsed = time.perf_counter() - idempotency_started
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
        real_integration_controls = _real_integration_controls(connection)
    idempotency_after = audit["row_counts"]
    idempotency_statuses = [
        refresh.status
        for result in idempotency_results
        if isinstance(result, SteamRefreshResult)
        for refresh in result.refreshes
    ]
    idempotency_errors = [
        repr(result) for result in idempotency_results if isinstance(result, Exception)
    ]
    idempotency_ok = bool(
        idempotency_before
        and idempotency_before == idempotency_after
        and not idempotency_errors
        and all(status in {"ready", "not_found"} for status in idempotency_statuses)
    )

    schema_contract_ok = bool(audit["ok"])
    parser_semantics_ok = bool(positive_controls["ok"])
    persistence_ok = integrity == "ok" and not foreign_key_errors
    benchmark_control_cases_ok = bool(
        positive_controls["ok"]
        and real_integration_controls["ok"]
        and coverage["core_semantics_ok"]
    )
    queue_statuses = Counter(state.status for state in states)
    benchmark: dict[str, Any] = {
        "source": "Steam",
        "deprecated_sources_skipped": ["Wikidata"],
        "started_at_utc": started_at_utc,
        "input_file": str(input_path),
        "limit": limit,
        "app_ids": app_ids,
        "observation_regions": ["KZ"],
        "database_file": str(database_path),
        "added_primary_tasks": added,
        "elapsed_seconds": round(elapsed, 4),
        "idempotency_elapsed_seconds": (
            round(idempotency_elapsed, 4) if idempotency_elapsed is not None else None
        ),
        "queue": {"total": len(states), "statuses": dict(queue_statuses)},
        "http_metrics": metrics.report(),
        "sqlite_integrity": integrity,
        "sqlite_foreign_key_errors": foreign_key_errors,
        "schema_audit": audit,
        "schema_contract_ok": schema_contract_ok,
        "parser_semantics_ok": parser_semantics_ok,
        "persistence_ok": persistence_ok,
        "idempotency_ok": idempotency_ok,
        "idempotency": {
            "before_row_counts": idempotency_before,
            "after_row_counts": idempotency_after,
            "refresh_statuses": dict(Counter(idempotency_statuses)),
            "errors": idempotency_errors,
        },
        "benchmark_control_cases_ok": benchmark_control_cases_ok,
        "source_coverage_ok": bool(coverage["ok"]),
        "positive_controls": positive_controls,
        "real_integration_controls": real_integration_controls,
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
        f"- idempotency_ok: `{idempotency_ok}` "
        f"(repeat elapsed: {benchmark['idempotency_elapsed_seconds']} s)",
        "",
        "## Coverage state",
        "",
        f"- Currency: `{coverage['known_currency']}/{coverage['total_price_rows']}` "
        "rows have authoritative Steam currency.",
        f"- Price regions: `{coverage['known_price_regions']}/{coverage['total_price_rows']}` "
        f"rows; distribution: `{coverage['price_regions']}`; "
        f"currencies: `{coverage['currencies']}`.",
        f"- Price consistency violations: `{coverage['price_consistency_violations']}`; "
        f"active discounts with end_at: `{coverage['active_discounts_with_end_at']}`.",
        f"- Price observations: `{coverage['price_observations']}`. Row absence is not "
        "counted as an unavailable region.",
        f"- Regional package semantics: `{coverage['regional_price_semantics']}`.",
        f"- Empty-string violations: `{len(coverage['empty_string_violations'])}` fields.",
        f"- Unresolved editions: `{coverage['unresolved_editions']}`.",
        f"- Build install-profile coverage: `{coverage['branch_profiles']['coverage']}`.",
        f"- Generic external links left: `{coverage['generic_external_links']}`.",
        f"- Unknown category diagnostics: `{coverage['categories']['diagnostics']}`.",
        f"- Achievements: `{coverage['achievements']['status']}` "
        f"({coverage['achievements']['rows']} base rows, "
        f"{coverage['achievements']['localization_rows']} localization rows).",
        f"- Supported-language invariant: `{coverage['supported_language_invariant']['ok']}`.",
        f"- Review-language NULL rows: `{coverage['review_language_nulls']}`.",
        f"- Rating minimum_age: `{coverage['rating_minimum_age']}`.",
        f"- Controllers: `{coverage['controller_types']}`; "
        f"transport: `{coverage['controller_transport']}`.",
        f"- Depots: `{coverage['depots']}`.",
        f"- Tags: `{coverage['tags']}`; genres: `{coverage['genres']}`.",
        f"- Organizations: `{coverage['organizations']}`; Workshop: `{coverage['workshop']}`.",
        f"- Duplicate diagnostics: `{coverage['duplicate_diagnostics']}`; "
        f"orphan packages/bundles: `{coverage['orphan_packages']}/{coverage['orphan_bundles']}`.",
        f"- Soundtrack catalog: `{coverage['soundtrack_catalog_coverage']['status']}`.",
        f"- Orphan bundle editions: `{coverage['orphan_bundle_edition_rows']}`.",
        f"- Bundle membership distribution: `{bundle_distribution_text}`.",
        "",
        "## Positive controls",
        "",
        f"- Passed: `{positive_controls['passed']}/{positive_controls['total']}`.",
        f"- Live source-backed controls: `{real_integration_controls['passed']}/"
        f"{real_integration_controls['total']}`.",
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
