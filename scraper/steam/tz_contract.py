"""Authoritative physical Steam schema contract derived from ``TZ.md``.

This module intentionally does not import SQLAlchemy models.  The benchmark
uses it as an independent check that can fail when the ORM drifts away from
the source-domain contract.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True, slots=True)
class ColumnContract:
    sqlite_type: str
    nullable: bool


@dataclass(frozen=True, slots=True)
class ForeignKeyContract:
    columns: tuple[str, ...]
    ref_table: str
    ref_columns: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class TableContract:
    domain: dict[str, ColumnContract]
    technical: dict[str, ColumnContract]
    primary_key: tuple[str, ...]
    unique_keys: tuple[tuple[str, ...], ...] = ()
    foreign_keys: tuple[ForeignKeyContract, ...] = ()
    literals: dict[str, frozenset[str]] = field(default_factory=dict)


def _c(sqlite_type: str, nullable: bool = False) -> ColumnContract:
    return ColumnContract(sqlite_type, nullable)


def _fk(
    columns: tuple[str, ...], ref_table: str, ref_columns: tuple[str, ...]
) -> ForeignKeyContract:
    return ForeignKeyContract(columns, ref_table, ref_columns)


T = TableContract


# This is deliberately verbose: adding a column to the ORM must not silently
# change the expected TZ contract.
STEAM_TZ_CONTRACT: dict[str, TableContract] = {
    "steam_apps": T(
        domain={
            "app_id": _c("INTEGER"),
            "type": _c("TEXT", True),
            "demo_id": _c("INTEGER", True),
            "dlc_for_app_id": _c("INTEGER", True),
            "optional_dlc": _c("BOOLEAN", True),
            "required_app_id": _c("INTEGER", True),
            "linux_build": _c("BOOLEAN", True),
            "windows_build": _c("BOOLEAN", True),
            "mac_build": _c("BOOLEAN", True),
            "vac_enabled": _c("BOOLEAN", True),
            "metacritic_score": _c("INTEGER", True),
            "metacritic_url": _c("TEXT", True),
            "gamepad_preferred": _c("BOOLEAN", True),
            "controller_support": _c("TEXT", True),
            "release_date": _c("DATE", True),
            "release_date_max": _c("DATE", True),
            "release_status": _c("TEXT", True),
            "external_account_notice": _c("TEXT", True),
            "drm_notice": _c("TEXT", True),
        },
        technical={},
        primary_key=("app_id",),
        literals={
            "type": frozenset({"game", "application", "dlc", "soundtrack"}),
            "release_status": frozenset(
                {"not_released", "advanced_access", "early_access", "released", "removed"}
            ),
            "controller_support": frozenset({"none", "partial", "full"}),
        },
    ),
    "steam_app_localizations": T(
        domain={
            "app_id": _c("INTEGER"),
            "language": _c("TEXT"),
            "name": _c("TEXT", True),
            "short_description": _c("TEXT", True),
            "about": _c("TEXT", True),
            "long_description": _c("TEXT", True),
            "legal_notice": _c("TEXT", True),
        },
        technical={},
        primary_key=("app_id", "language"),
        foreign_keys=(_fk(("app_id",), "steam_apps", ("app_id",)),),
    ),
    "steam_media": T(
        domain={
            "app_id": _c("INTEGER"),
            "media_type": _c("TEXT"),
            "url": _c("TEXT"),
            "format": _c("TEXT", True),
            "language": _c("TEXT", True),
        },
        technical={"id": _c("INTEGER")},
        primary_key=("id",),
        unique_keys=(("app_id", "media_type", "url", "language"),),
        foreign_keys=(_fk(("app_id",), "steam_apps", ("app_id",)),),
        literals={
            "media_type": frozenset(
                {
                    "screenshot",
                    "trailer",
                    "header_capsule",
                    "small_capsule",
                    "main_capsule",
                    "vertical_capsule",
                    "page_background",
                    "library_capsule",
                    "library_header",
                    "library_hero",
                    "library_logo",
                }
            )
        },
    ),
    "steam_app_editions": T(
        domain={"app_id": _c("INTEGER"), "package_id": _c("INTEGER")},
        technical={},
        primary_key=("app_id", "package_id"),
        foreign_keys=(
            _fk(("app_id",), "steam_apps", ("app_id",)),
            _fk(("package_id",), "steam_editions", ("package_id",)),
        ),
    ),
    "steam_editions": T(
        domain={
            "package_id": _c("INTEGER"),
            "name": _c("TEXT", True),
            "description": _c("TEXT", True),
            "resolved": _c("BOOLEAN"),
        },
        technical={},
        primary_key=("package_id",),
    ),
    "steam_edition_prices": T(
        domain={
            "package_id": _c("INTEGER"),
            "currency": _c("TEXT"),
            "initial": _c("INTEGER", True),
            "final": _c("INTEGER", True),
            "discount_percent": _c("INTEGER", True),
            "price_type": _c("TEXT", True),
            "period": _c("TEXT", True),
            "period_units": _c("INTEGER", True),
        },
        technical={},
        primary_key=("package_id", "currency"),
        foreign_keys=(_fk(("package_id",), "steam_editions", ("package_id",)),),
        literals={
            "price_type": frozenset({"one_time", "recurring"}),
            "period": frozenset(
                {
                    "hour",
                    "day",
                    "week",
                    "month",
                    "year",
                }
            ),
        },
    ),
    "steam_bundles": T(
        domain={
            "bundle_id": _c("INTEGER"),
            "name": _c("TEXT", True),
            "discount_percent": _c("INTEGER", True),
            "must_purchase_as_set": _c("BOOLEAN", True),
        },
        technical={},
        primary_key=("bundle_id",),
    ),
    "steam_bundle_editions": T(
        domain={"bundle_id": _c("INTEGER"), "package_id": _c("INTEGER")},
        technical={},
        primary_key=("bundle_id", "package_id"),
        foreign_keys=(
            _fk(("bundle_id",), "steam_bundles", ("bundle_id",)),
            _fk(("package_id",), "steam_editions", ("package_id",)),
        ),
    ),
    "steam_bundle_prices": T(
        domain={
            "bundle_id": _c("INTEGER"),
            "currency": _c("TEXT"),
            "effective_discount_percent": _c("INTEGER", True),
            "initial": _c("INTEGER", True),
            "final": _c("INTEGER", True),
        },
        technical={},
        primary_key=("bundle_id", "currency"),
        foreign_keys=(_fk(("bundle_id",), "steam_bundles", ("bundle_id",)),),
    ),
    "steam_external_links": T(
        domain={
            "app_id": _c("INTEGER"),
            "type": _c("TEXT"),
            "url": _c("TEXT", True),
            "value": _c("TEXT", True),
        },
        technical={"id": _c("INTEGER")},
        primary_key=("id",),
        foreign_keys=(_fk(("app_id",), "steam_apps", ("app_id",)),),
    ),
    "steam_age_ratings": T(
        domain={
            "app_id": _c("INTEGER"),
            "standard": _c("TEXT"),
            "age_id": _c("TEXT"),
            "rating_generated": _c("BOOLEAN", True),
            "use_age_gate": _c("BOOLEAN", True),
            "banned": _c("BOOLEAN", True),
            "rating": _c("TEXT", True),
            "minimum_age": _c("INTEGER", True),
            "descriptor_raw": _c("TEXT", True),
        },
        technical={},
        primary_key=("age_id",),
        foreign_keys=(_fk(("app_id",), "steam_apps", ("app_id",)),),
    ),
    "steam_descriptors": T(
        domain={"age_id": _c("TEXT"), "steam_id": _c("INTEGER", True), "name": _c("TEXT", True)},
        technical={"id": _c("INTEGER")},
        primary_key=("id",),
        unique_keys=(("age_id", "steam_id", "name"),),
        foreign_keys=(_fk(("age_id",), "steam_age_ratings", ("age_id",)),),
    ),
    "steam_system_requirements": T(
        domain={
            "app_id": _c("INTEGER"),
            "platform": _c("TEXT"),
            "level": _c("TEXT"),
            "html": _c("TEXT"),
        },
        technical={"id": _c("INTEGER")},
        primary_key=("id",),
        foreign_keys=(_fk(("app_id",), "steam_apps", ("app_id",)),),
    ),
    "steam_features": T(
        domain={
            "app_id": _c("INTEGER"),
            "category_id": _c("INTEGER"),
            "english_name": _c("TEXT", True),
        },
        technical={},
        primary_key=("app_id", "category_id"),
        foreign_keys=(_fk(("app_id",), "steam_apps", ("app_id",)),),
    ),
    "steam_accessibility_features": T(
        domain={
            "app_id": _c("INTEGER"),
            "category_id": _c("INTEGER"),
            "english_name": _c("TEXT", True),
        },
        technical={},
        primary_key=("app_id", "category_id"),
        foreign_keys=(_fk(("app_id",), "steam_apps", ("app_id",)),),
    ),
    "steam_deck_support": T(
        domain={"app_id": _c("INTEGER"), "status": _c("TEXT")},
        technical={},
        primary_key=("app_id",),
        foreign_keys=(_fk(("app_id",), "steam_apps", ("app_id",)),),
        literals={"status": frozenset({"unknown", "unsupported", "playable", "supported"})},
    ),
    "steam_eulas": T(
        domain={
            "app_id": _c("INTEGER"),
            "eula_id": _c("TEXT", True),
            "name_description": _c("TEXT", True),
            "url": _c("TEXT", True),
            "version": _c("TEXT", True),
        },
        technical={"id": _c("INTEGER")},
        primary_key=("id",),
        unique_keys=(("app_id", "eula_id"),),
        foreign_keys=(_fk(("app_id",), "steam_apps", ("app_id",)),),
    ),
    "steam_controllers": T(
        domain={
            "app_id": _c("INTEGER"),
            "controller": _c("TEXT"),
            "support": _c("BOOLEAN", True),
            "bluetooth": _c("BOOLEAN", True),
            "usb": _c("BOOLEAN", True),
        },
        technical={},
        primary_key=("app_id", "controller"),
        foreign_keys=(_fk(("app_id",), "steam_apps", ("app_id",)),),
    ),
    "steam_organization_credits": T(
        domain={"app_id": _c("INTEGER"), "status": _c("TEXT"), "organization_name": _c("TEXT")},
        technical={"id": _c("INTEGER")},
        primary_key=("id",),
        unique_keys=(("app_id", "status", "organization_name"),),
        foreign_keys=(_fk(("app_id",), "steam_apps", ("app_id",)),),
    ),
    "steam_supported_languages": T(
        domain={
            "app_id": _c("INTEGER"),
            "language": _c("TEXT"),
            "audio": _c("BOOLEAN", True),
            "text": _c("BOOLEAN", True),
            "subtitles": _c("BOOLEAN", True),
        },
        technical={},
        primary_key=("app_id", "language"),
        foreign_keys=(_fk(("app_id",), "steam_apps", ("app_id",)),),
    ),
    "steam_build_branches": T(
        domain={
            "app_id": _c("INTEGER"),
            "name": _c("TEXT"),
            "updated_at": _c("DATETIME", True),
            "description": _c("TEXT", True),
            "build_id": _c("INTEGER", True),
            "download_size_min": _c("INTEGER", True),
            "download_size_median": _c("INTEGER", True),
            "download_size_max": _c("INTEGER", True),
            "disk_size_min": _c("INTEGER", True),
            "disk_size_median": _c("INTEGER", True),
            "disk_size_max": _c("INTEGER", True),
        },
        technical={},
        primary_key=("app_id", "name"),
        foreign_keys=(_fk(("app_id",), "steam_apps", ("app_id",)),),
    ),
    "steam_review_language_stats": T(
        domain={
            "app_id": _c("INTEGER"),
            "language": _c("TEXT"),
            "total_reviews": _c("INTEGER"),
            "total_negative": _c("INTEGER"),
            "total_positive": _c("INTEGER"),
            "review_score": _c("INTEGER", True),
        },
        technical={"id": _c("INTEGER")},
        primary_key=("id",),
        unique_keys=(("app_id", "language"),),
        foreign_keys=(_fk(("app_id",), "steam_apps", ("app_id",)),),
    ),
    "steam_reviews": T(
        domain={
            "app_id": _c("INTEGER"),
            "user_id": _c("TEXT", True),
            "playtime_forever": _c("INTEGER", True),
            "playtime_last_two_weeks": _c("INTEGER", True),
            "playtime_at_review": _c("INTEGER", True),
            "deck_playtime_at_review": _c("INTEGER", True),
            "datetime_last_played": _c("DATETIME", True),
            "datetime_created": _c("DATETIME", True),
            "datetime_updated": _c("DATETIME", True),
            "datetime_dev_responded": _c("DATETIME", True),
            "votes_up": _c("INTEGER"),
            "votes_funny": _c("INTEGER"),
            "weighted_vote_score": _c("FLOAT", True),
            "comment_count": _c("INTEGER"),
            "steam_purchase": _c("BOOLEAN", True),
            "received_for_free": _c("BOOLEAN", True),
            "written_during_early_access": _c("BOOLEAN", True),
            "primarily_steam_deck": _c("BOOLEAN", True),
            "voted_up": _c("BOOLEAN", True),
            "language": _c("TEXT", True),
            "review_text": _c("TEXT"),
            "developer_response": _c("TEXT", True),
        },
        technical={"id": _c("INTEGER"), "recommendation_id": _c("TEXT")},
        primary_key=("id",),
        unique_keys=(("app_id", "recommendation_id"),),
        foreign_keys=(_fk(("app_id",), "steam_apps", ("app_id",)),),
    ),
    "steam_external_reviews": T(
        domain={
            "app_id": _c("INTEGER"),
            "organization": _c("TEXT"),
            "rating": _c("TEXT", True),
            "url": _c("TEXT", True),
            "quote": _c("TEXT", True),
        },
        technical={"id": _c("INTEGER")},
        primary_key=("id",),
        foreign_keys=(_fk(("app_id",), "steam_apps", ("app_id",)),),
    ),
    "steam_achievements": T(
        domain={
            "app_id": _c("INTEGER"),
            "achievement_id": _c("TEXT"),
            "icon_url": _c("TEXT", True),
            "global_percent": _c("FLOAT", True),
            "hidden": _c("BOOLEAN", True),
        },
        technical={},
        primary_key=("app_id", "achievement_id"),
        foreign_keys=(_fk(("app_id",), "steam_apps", ("app_id",)),),
    ),
    "steam_achievement_localizations": T(
        domain={
            "app_id": _c("INTEGER"),
            "achievement_id": _c("TEXT"),
            "language": _c("TEXT"),
            "name": _c("TEXT"),
            "description": _c("TEXT", True),
        },
        technical={},
        primary_key=("app_id", "achievement_id", "language"),
        foreign_keys=(
            _fk(("app_id", "achievement_id"), "steam_achievements", ("app_id", "achievement_id")),
        ),
    ),
}


def _actual_columns(connection: sqlite3.Connection, table: str) -> dict[str, tuple[str, bool, int]]:
    result: dict[str, tuple[str, bool, int]] = {}
    for _, name, type_name, not_null, _default, pk in connection.execute(
        f'PRAGMA table_info("{table}")'
    ):
        result[name] = (str(type_name or "").upper(), bool(not_null == 0 and pk == 0), int(pk))
    return result


def _type_family(type_name: str) -> str:
    normalized = type_name.upper()
    if "INT" in normalized:
        return "INTEGER"
    if any(token in normalized for token in ("CHAR", "CLOB", "TEXT")):
        return "TEXT"
    if any(token in normalized for token in ("REAL", "FLOA", "DOUB")):
        return "FLOAT"
    if "DATE" in normalized and "TIME" not in normalized:
        return "DATE"
    if "TIME" in normalized:
        return "DATETIME"
    if "BOOL" in normalized:
        return "BOOLEAN"
    return normalized


def _actual_unique_keys(connection: sqlite3.Connection, table: str) -> set[tuple[str, ...]]:
    result: set[tuple[str, ...]] = set()
    for _, index_name, is_unique, *_ in connection.execute(f'PRAGMA index_list("{table}")'):
        if not is_unique:
            continue
        columns = tuple(
            row[2]
            for row in sorted(
                connection.execute(f'PRAGMA index_info("{index_name}")'), key=lambda r: r[0]
            )
        )
        if columns:
            result.add(columns)
    return result


def _actual_foreign_keys(connection: sqlite3.Connection, table: str) -> set[ForeignKeyContract]:
    grouped: dict[int, list[tuple[int, str, str, str]]] = {}
    for row in connection.execute(f'PRAGMA foreign_key_list("{table}")'):
        grouped.setdefault(int(row[0]), []).append((int(row[1]), row[2], row[3], row[4]))
    result: set[ForeignKeyContract] = set()
    for rows in grouped.values():
        ordered = sorted(rows)
        result.add(
            ForeignKeyContract(
                tuple(row[2] for row in ordered),
                ordered[0][1],
                tuple(row[3] for row in ordered),
            )
        )
    return result


def _foreign_key_payload(value: ForeignKeyContract) -> dict[str, Any]:
    return {
        "columns": list(value.columns),
        "ref_table": value.ref_table,
        "ref_columns": list(value.ref_columns),
    }


def audit_steam_tz_schema(connection: sqlite3.Connection) -> dict[str, Any]:
    tables = {
        row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")
    }
    per_table: dict[str, Any] = {}
    errors: list[str] = []
    for table, contract in STEAM_TZ_CONTRACT.items():
        actual = _actual_columns(connection, table) if table in tables else {}
        actual_names = set(actual)
        domain_names = set(contract.domain)
        technical_names = set(contract.technical)
        item: dict[str, Any] = {
            "missing_domain_columns": sorted(domain_names - actual_names),
            "unexpected_domain_columns": sorted(actual_names - domain_names - technical_names),
            "allowed_technical_columns": sorted(actual_names & technical_names),
            "type_nullability_mismatch": [],
            "primary_key_mismatch": None,
            "unique_key_mismatch": {"missing": [], "unexpected": []},
            "foreign_key_mismatch": {"missing": [], "unexpected": []},
        }
        if table not in tables:
            errors.append(f"missing table {table}")
            per_table[table] = item
            continue
        for name, spec in {**contract.domain, **contract.technical}.items():
            if name not in actual:
                continue
            actual_type, actual_nullable, _ = actual[name]
            if (
                _type_family(actual_type) != _type_family(spec.sqlite_type)
                or actual_nullable != spec.nullable
            ):
                item["type_nullability_mismatch"].append(
                    {
                        "column": name,
                        "expected": {"type": spec.sqlite_type, "nullable": spec.nullable},
                        "actual": {"type": actual_type, "nullable": actual_nullable},
                    }
                )
        pk = tuple(
            name
            for name, (_, _, position) in sorted(actual.items(), key=lambda x: x[1][2])
            if position
        )
        item["primary_key_mismatch"] = (
            {"expected": contract.primary_key, "actual": pk} if pk != contract.primary_key else None
        )
        actual_unique = _actual_unique_keys(connection, table)
        expected_unique = set(contract.unique_keys)
        item["unique_key_mismatch"] = {
            "missing": sorted(expected_unique - actual_unique),
            "unexpected": sorted(actual_unique - expected_unique - {contract.primary_key}),
        }
        actual_fk = _actual_foreign_keys(connection, table)
        expected_fk = set(contract.foreign_keys)
        item["foreign_key_mismatch"] = {
            "missing": [
                _foreign_key_payload(value)
                for value in sorted(expected_fk - actual_fk, key=repr)
            ],
            "unexpected": [
                _foreign_key_payload(value)
                for value in sorted(actual_fk - expected_fk, key=repr)
            ],
        }
        per_table[table] = item
        for key in (
            "missing_domain_columns",
            "unexpected_domain_columns",
            "type_nullability_mismatch",
        ):
            if item[key]:
                errors.append(f"{table}: {key}={item[key]}")
        if item["primary_key_mismatch"]:
            errors.append(f"{table}: primary key mismatch")
        if any(item["unique_key_mismatch"].values()):
            errors.append(f"{table}: unique key mismatch")
        if any(item["foreign_key_mismatch"].values()):
            errors.append(f"{table}: foreign key mismatch")
    unexpected_tables = sorted(
        tables - set(STEAM_TZ_CONTRACT) - {"source_refreshes", "source_diagnostics"}
    )
    if unexpected_tables:
        errors.append(f"unexpected tables: {unexpected_tables}")
    return {
        "ok": not errors,
        "errors": errors,
        "tables": sorted(tables),
        "per_table": per_table,
    }


__all__ = ["STEAM_TZ_CONTRACT", "audit_steam_tz_schema"]
