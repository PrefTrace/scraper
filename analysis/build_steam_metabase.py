from __future__ import annotations

import ast
import csv
import io
import json
import math
import re
import sqlite3
import zipfile
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable


DOWNLOADS = Path(r"C:\Users\miskl\Downloads")
OUTPUT = Path(__file__).resolve().parents[1] / "outputs" / "steam_metabase"
DB_PATH = OUTPUT / "steam_metabase.sqlite"


SOURCES = [
    {
        "path": DOWNLOADS / "games_may2024_cleaned.csv",
        "dataset": "artermiloff/steam-games-dataset",
        "source_file": "games_may2024_cleaned.csv",
        "snapshot_date": "2024-05-31",
        "snapshot_label": "may_2024",
        "raw_header_fix": "none",
    },
    {
        "path": DOWNLOADS / "games_march2025_cleaned.csv",
        "dataset": "artermiloff/steam-games-dataset",
        "source_file": "games_march2025_cleaned.csv",
        "snapshot_date": "2025-03-13",
        "snapshot_label": "march_2025",
        "raw_header_fix": "none",
    },
    {
        "path": DOWNLOADS / "games.csv",
        "dataset": "fronkongames/steam-games-dataset",
        "source_file": "games.csv",
        "snapshot_date": "2026-08-19",
        "snapshot_label": "current_2026",
        "raw_header_fix": "split DiscountDLC count into Discount and DLC count",
    },
]

REVIEW_ZIP = DOWNLOADS / "weighted_score_above_08.csv.zip"
REVIEW_FILE = "weighted_score_above_08.csv"
RECOMMENDATION_ZIP = DOWNLOADS / "recommendations.csv.zip"
RECOMMENDATION_FILE = "recommendations.csv"


def clean_name(value: str) -> str:
    value = value.strip().lower()
    value = value.replace("%", "pct")
    value = re.sub(r"[^a-z0-9]+", "_", value)
    return value.strip("_")


def normalise_header(header: list[str], *, repair_current: bool) -> tuple[list[str], str]:
    header = [item.strip().lstrip("\ufeff") for item in header]
    fix = "none"
    if repair_current and "DiscountDLC count" in header:
        index = header.index("DiscountDLC count")
        header = header[:index] + ["Discount", "DLC count"] + header[index + 1 :]
        fix = "split DiscountDLC count into Discount and DLC count"
    return [clean_name(item) for item in header], fix


def safe_int(value: Any) -> int | None:
    if value is None:
        return None
    text = str(value).strip().replace(",", "")
    if not text:
        return None
    try:
        return int(float(text))
    except (TypeError, ValueError):
        return None


def safe_float(value: Any) -> float | None:
    if value is None:
        return None
    text = str(value).strip().replace(",", "")
    if not text:
        return None
    try:
        number = float(text)
        return number if math.isfinite(number) else None
    except (TypeError, ValueError):
        return None


def safe_bool(value: Any) -> int | None:
    if value is None:
        return None
    text = str(value).strip().lower()
    if text in {"true", "1", "yes", "y"}:
        return 1
    if text in {"false", "0", "no", "n"}:
        return 0
    return None


def parse_date(value: Any) -> str | None:
    text = str(value or "").strip()
    if not text:
        return None
    for fmt in ("%Y-%m-%d", "%b %d, %Y", "%B %d, %Y"):
        try:
            return datetime.strptime(text, fmt).date().isoformat()
        except ValueError:
            continue
    return None


def parse_owner_range(value: Any) -> tuple[int | None, int | None, float | None]:
    text = str(value or "")
    numbers = [safe_int(item) for item in re.findall(r"\d[\d,]*", text)]
    numbers = [item for item in numbers if item is not None]
    if not numbers:
        return None, None, None
    if len(numbers) == 1:
        low = high = numbers[0]
    else:
        low, high = numbers[0], numbers[1]
    return low, high, (low + high) / 2


def parse_structured(value: Any) -> Any:
    text = str(value or "").strip()
    if not text:
        return []
    try:
        parsed = ast.literal_eval(text)
    except (SyntaxError, ValueError):
        # The current FronkonGames CSV uses comma-separated text for several
        # multi-value fields, while the older snapshots use Python literals.
        return [item.strip() for item in text.split(",") if item.strip()]
    return parsed


def json_text(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def wilson_lower(positive: int | None, negative: int | None) -> float | None:
    pos = positive or 0
    neg = negative or 0
    total = pos + neg
    if total == 0:
        return None
    z = 1.959963984540054
    p = pos / total
    denominator = 1 + z * z / total
    centre = p + z * z / (2 * total)
    spread = z * math.sqrt((p * (1 - p) + z * z / (4 * total)) / total)
    return (centre - spread) / denominator


def open_csv(path: Path) -> tuple[io.TextIOBase, csv.reader, list[str], list[str], str]:
    handle = path.open("r", encoding="utf-8-sig", errors="replace", newline="")
    reader = csv.reader(handle)
    raw_header = next(reader)
    repair_current = path.name == "games.csv"
    header, fix = normalise_header(raw_header, repair_current=repair_current)
    return handle, reader, raw_header, header, fix


def audit_game_file(spec: dict[str, str | Path]) -> dict[str, Any]:
    path = Path(spec["path"])
    handle, reader, raw_header, header, fix = open_csv(path)
    expected_width = len(header)
    width_counts: Counter[int] = Counter()
    bad_width_examples: list[dict[str, Any]] = []
    invalid = Counter()
    rows = 0
    try:
        for row in reader:
            rows += 1
            width_counts[len(row)] += 1
            if len(row) != expected_width and len(bad_width_examples) < 5:
                bad_width_examples.append({"row": rows, "width": len(row), "values": row[:12]})
            if len(row) < expected_width:
                continue
            values = dict(zip(header, row))
            if safe_int(values.get("appid")) is None:
                invalid["appid"] += 1
            if values.get("release_date") and parse_date(values.get("release_date")) is None:
                invalid["release_date"] += 1
            if values.get("price") and safe_float(values.get("price")) is None:
                invalid["price"] += 1
            for key in ("windows", "mac", "linux"):
                if values.get(key) and safe_bool(values.get(key)) is None:
                    invalid[key] += 1
            for key in ("positive", "negative", "peak_ccu"):
                if values.get(key) and safe_int(values.get(key)) is None:
                    invalid[key] += 1
    finally:
        handle.close()
    return {
        "source_file": path.name,
        "bytes": path.stat().st_size,
        "raw_header_fields": len(raw_header),
        "normalised_header_fields": len(header),
        "rows": rows,
        "row_widths": dict(width_counts),
        "bad_width_examples": bad_width_examples,
        "invalid_semantic_values": dict(invalid),
        "header_fix": fix,
        "status": "pass" if set(width_counts) == {expected_width} and not invalid else "review",
        "normalised_header": header,
    }


def audit_zip(path: Path, member_name: str, expected_header: list[str]) -> dict[str, Any]:
    with zipfile.ZipFile(path) as archive:
        info = archive.getinfo(member_name)
        with archive.open(info) as raw:
            text = io.TextIOWrapper(raw, encoding="utf-8-sig", errors="replace", newline="")
            reader = csv.reader(text)
            header = next(reader)
            widths: Counter[int] = Counter()
            rows = 0
            for row in reader:
                rows += 1
                widths[len(row)] += 1
                if rows >= 100_000:
                    break
    normalised = [clean_name(item) for item in header]
    return {
        "source_file": f"{path.name}:{member_name}",
        "bytes": path.stat().st_size,
        "member_bytes": info.file_size,
        "header_fields": len(header),
        "header": normalised,
        "expected_header": expected_header,
        "sample_rows_checked": rows,
        "sample_row_widths": dict(widths),
        "status": "pass" if normalised == expected_header and set(widths) == {len(header)} else "review",
    }


def create_schema(connection: sqlite3.Connection) -> None:
    connection.executescript(
        """
        PRAGMA journal_mode = OFF;
        PRAGMA synchronous = OFF;
        PRAGMA temp_store = MEMORY;
        PRAGMA foreign_keys = OFF;

        DROP TABLE IF EXISTS source_manifest;
        DROP TABLE IF EXISTS data_quality_check;
        DROP TABLE IF EXISTS dim_game;
        DROP TABLE IF EXISTS game_snapshot;
        DROP TABLE IF EXISTS game_genre;
        DROP TABLE IF EXISTS game_tag;
        DROP TABLE IF EXISTS game_developer;
        DROP TABLE IF EXISTS game_publisher;
        DROP TABLE IF EXISTS game_category;
        DROP TABLE IF EXISTS review_game;
        DROP TABLE IF EXISTS review_game_month;
        DROP TABLE IF EXISTS recommendation_game;
        DROP TABLE IF EXISTS recommendation_game_month;
        DROP TABLE IF EXISTS join_coverage;
        DROP TABLE IF EXISTS market_year_summary;
        DROP TABLE IF EXISTS game_snapshot_comparison;

        CREATE TABLE source_manifest (
            source_file TEXT PRIMARY KEY,
            dataset TEXT NOT NULL,
            snapshot_date TEXT,
            snapshot_label TEXT,
            bytes INTEGER,
            rows INTEGER,
            header_fields INTEGER,
            header_fix TEXT,
            license TEXT,
            notes TEXT
        );

        CREATE TABLE data_quality_check (
            source_file TEXT,
            check_name TEXT,
            check_value TEXT,
            status TEXT,
            details TEXT
        );

        CREATE TABLE dim_game (
            appid INTEGER PRIMARY KEY,
            name TEXT,
            release_date TEXT,
            release_year INTEGER,
            first_seen_snapshot TEXT,
            last_seen_snapshot TEXT,
            snapshot_count INTEGER,
            source_count INTEGER,
            developers_json TEXT,
            publishers_json TEXT,
            genres_json TEXT,
            tags_json TEXT
        );

        CREATE TABLE game_snapshot (
            appid INTEGER NOT NULL,
            snapshot_date TEXT NOT NULL,
            snapshot_label TEXT NOT NULL,
            source_dataset TEXT NOT NULL,
            source_file TEXT NOT NULL,
            name TEXT,
            release_date TEXT,
            release_year INTEGER,
            required_age INTEGER,
            price REAL,
            discount REAL,
            dlc_count INTEGER,
            is_free INTEGER,
            estimated_owners TEXT,
            owner_low INTEGER,
            owner_high INTEGER,
            owner_mid REAL,
            peak_ccu INTEGER,
            positive INTEGER,
            negative INTEGER,
            total_reviews INTEGER,
            positive_share REAL,
            wilson_lower REAL,
            pct_pos_recent REAL,
            num_reviews_recent INTEGER,
            average_playtime_forever INTEGER,
            average_playtime_2weeks INTEGER,
            median_playtime_forever INTEGER,
            median_playtime_2weeks INTEGER,
            user_score REAL,
            metacritic_score INTEGER,
            windows INTEGER,
            mac INTEGER,
            linux INTEGER,
            developers_json TEXT,
            publishers_json TEXT,
            categories_json TEXT,
            genres_json TEXT,
            tags_json TEXT,
            raw_header_fields INTEGER,
            row_field_count INTEGER,
            alignment_repaired INTEGER,
            PRIMARY KEY (appid, snapshot_date, source_dataset)
        );

        CREATE TABLE game_genre (
            appid INTEGER,
            snapshot_date TEXT,
            genre TEXT,
            PRIMARY KEY (appid, snapshot_date, genre)
        );
        CREATE TABLE game_tag (
            appid INTEGER,
            snapshot_date TEXT,
            tag TEXT,
            tag_score INTEGER,
            PRIMARY KEY (appid, snapshot_date, tag)
        );
        CREATE TABLE game_developer (
            appid INTEGER,
            snapshot_date TEXT,
            developer TEXT,
            PRIMARY KEY (appid, snapshot_date, developer)
        );
        CREATE TABLE game_publisher (
            appid INTEGER,
            snapshot_date TEXT,
            publisher TEXT,
            PRIMARY KEY (appid, snapshot_date, publisher)
        );
        CREATE TABLE game_category (
            appid INTEGER,
            snapshot_date TEXT,
            category TEXT,
            PRIMARY KEY (appid, snapshot_date, category)
        );

        CREATE TABLE review_game (
            appid INTEGER PRIMARY KEY,
            sample_reviews INTEGER,
            positive_reviews INTEGER,
            negative_reviews INTEGER,
            positive_share REAL,
            avg_playtime_at_review_hours REAL,
            avg_helpful_votes REAL,
            purchased_share REAL,
            free_received_share REAL,
            early_access_share REAL,
            language_count INTEGER,
            first_review_date TEXT,
            last_review_date TEXT,
            source_filter TEXT
        );
        CREATE TABLE review_game_month (
            appid INTEGER,
            year_month TEXT,
            sample_reviews INTEGER,
            positive_reviews INTEGER,
            negative_reviews INTEGER,
            positive_share REAL,
            avg_playtime_at_review_hours REAL,
            avg_helpful_votes REAL,
            purchased_share REAL,
            early_access_share REAL,
            source_filter TEXT,
            PRIMARY KEY (appid, year_month)
        );

        CREATE TABLE recommendation_game (
            appid INTEGER PRIMARY KEY,
            interactions INTEGER,
            positive_interactions INTEGER,
            negative_interactions INTEGER,
            positive_share REAL,
            unique_users INTEGER,
            avg_hours REAL,
            avg_helpful_votes REAL,
            avg_funny_votes REAL,
            first_interaction_date TEXT,
            last_interaction_date TEXT
        );
        CREATE TABLE recommendation_game_month (
            appid INTEGER,
            year_month TEXT,
            interactions INTEGER,
            positive_interactions INTEGER,
            negative_interactions INTEGER,
            positive_share REAL,
            avg_hours REAL,
            avg_helpful_votes REAL,
            avg_funny_votes REAL,
            PRIMARY KEY (appid, year_month)
        );

        CREATE TABLE join_coverage (
            source_table TEXT PRIMARY KEY,
            fact_rows INTEGER,
            distinct_appids INTEGER,
            matched_appids INTEGER,
            match_share REAL
        );

        CREATE TABLE market_year_summary (
            snapshot_date TEXT,
            release_year INTEGER,
            games INTEGER,
            free_share REAL,
            median_price REAL,
            median_reviews REAL,
            median_peak_ccu REAL,
            median_positive_share REAL,
            PRIMARY KEY (snapshot_date, release_year)
        );
        CREATE TABLE game_snapshot_comparison (
            appid INTEGER PRIMARY KEY,
            name_2024 TEXT,
            name_2025 TEXT,
            name_2026 TEXT,
            release_date_2024 TEXT,
            release_date_2025 TEXT,
            release_date_2026 TEXT,
            reviews_2024 INTEGER,
            reviews_2025 INTEGER,
            reviews_2026 INTEGER,
            peak_ccu_2024 INTEGER,
            peak_ccu_2025 INTEGER,
            peak_ccu_2026 INTEGER,
            price_2024 REAL,
            price_2025 REAL,
            price_2026 REAL,
            owner_mid_2024 REAL,
            owner_mid_2025 REAL,
            owner_mid_2026 REAL,
            status_2024_2025 TEXT,
            status_2025_2026 TEXT,
            review_growth_2024_2025 REAL,
            review_growth_2025_2026 REAL
        );

        CREATE INDEX idx_game_snapshot_appid ON game_snapshot(appid);
        CREATE INDEX idx_game_snapshot_release_year ON game_snapshot(release_year);
        CREATE INDEX idx_game_snapshot_snapshot_date ON game_snapshot(snapshot_date);
        CREATE INDEX idx_game_genre_genre ON game_genre(genre);
        CREATE INDEX idx_game_tag_tag ON game_tag(tag);
        CREATE INDEX idx_review_game_month_date ON review_game_month(year_month);
        CREATE INDEX idx_recommendation_game_month_date ON recommendation_game_month(year_month);
        """
    )


def value(values: dict[str, str], *names: str) -> str:
    for name in names:
        if name in values:
            return values[name]
    return ""


def parse_game_row(
    row: list[str],
    header: list[str],
    spec: dict[str, str | Path],
    raw_header_fields: int,
) -> tuple[dict[str, Any], list[tuple[str, Any]], list[tuple[str, Any]], list[tuple[str, Any]], list[tuple[str, Any]], list[tuple[str, Any]]]:
    values = dict(zip(header, row))
    appid = safe_int(value(values, "appid"))
    if appid is None:
        raise ValueError("invalid appid")
    positive = safe_int(value(values, "positive")) or 0
    negative = safe_int(value(values, "negative")) or 0
    explicit_total = safe_int(value(values, "num_reviews_total", "reviews"))
    total_reviews = explicit_total if explicit_total is not None and explicit_total > 0 else positive + negative
    low, high, mid = parse_owner_range(value(values, "estimated_owners"))
    release_date = parse_date(value(values, "release_date"))
    release_year = int(release_date[:4]) if release_date else None
    genres = parse_structured(value(values, "genres"))
    tags = parse_structured(value(values, "tags"))
    developers = parse_structured(value(values, "developers"))
    publishers = parse_structured(value(values, "publishers"))
    categories = parse_structured(value(values, "categories"))
    if not isinstance(genres, list):
        genres = []
    if not isinstance(developers, list):
        developers = []
    if not isinstance(publishers, list):
        publishers = []
    if not isinstance(categories, list):
        categories = []
    if not isinstance(tags, dict):
        tags = {str(item): None for item in tags} if isinstance(tags, list) else {}
    positive_share = safe_float(value(values, "pct_pos_total"))
    if positive_share is None or not 0 <= positive_share <= 100:
        positive_share = None
    if positive_share is None and total_reviews:
        positive_share = positive * 100.0 / total_reviews
    record = {
        "appid": appid,
        "snapshot_date": str(spec["snapshot_date"]),
        "snapshot_label": str(spec["snapshot_label"]),
        "source_dataset": str(spec["dataset"]),
        "source_file": str(spec["source_file"]),
        "name": value(values, "name") or None,
        "release_date": release_date,
        "release_year": release_year,
        "required_age": safe_int(value(values, "required_age")),
        "price": safe_float(value(values, "price")),
        "discount": safe_float(value(values, "discount")),
        "dlc_count": safe_int(value(values, "dlc_count")),
        "is_free": 1 if safe_float(value(values, "price")) == 0 else 0,
        "estimated_owners": value(values, "estimated_owners") or None,
        "owner_low": low,
        "owner_high": high,
        "owner_mid": mid,
        "peak_ccu": safe_int(value(values, "peak_ccu")),
        "positive": positive,
        "negative": negative,
        "total_reviews": total_reviews,
        "positive_share": positive_share,
        "wilson_lower": wilson_lower(positive, negative),
        "pct_pos_recent": safe_float(value(values, "pct_pos_recent")),
        "num_reviews_recent": safe_int(value(values, "num_reviews_recent")),
        "average_playtime_forever": safe_int(value(values, "average_playtime_forever")),
        "average_playtime_2weeks": safe_int(value(values, "average_playtime_2weeks", "average_playtime_two_weeks")),
        "median_playtime_forever": safe_int(value(values, "median_playtime_forever")),
        "median_playtime_2weeks": safe_int(value(values, "median_playtime_2weeks", "median_playtime_two_weeks")),
        "user_score": safe_float(value(values, "user_score")),
        "metacritic_score": safe_int(value(values, "metacritic_score")),
        "windows": safe_bool(value(values, "windows")),
        "mac": safe_bool(value(values, "mac")),
        "linux": safe_bool(value(values, "linux")),
        "developers_json": json_text(developers),
        "publishers_json": json_text(publishers),
        "categories_json": json_text(categories),
        "genres_json": json_text(genres),
        "tags_json": json_text(tags),
        "raw_header_fields": raw_header_fields,
        "row_field_count": len(row),
        "alignment_repaired": 1 if str(spec["source_file"]) == "games.csv" else 0,
    }
    genres_rows = [(appid, record["snapshot_date"], str(item)) for item in genres if str(item).strip()]
    tags_rows = [(appid, record["snapshot_date"], str(item), safe_int(score)) for item, score in tags.items() if str(item).strip()]
    developer_rows = [(appid, record["snapshot_date"], str(item)) for item in developers if str(item).strip()]
    publisher_rows = [(appid, record["snapshot_date"], str(item)) for item in publishers if str(item).strip()]
    category_rows = [(appid, record["snapshot_date"], str(item)) for item in categories if str(item).strip()]
    return record, genres_rows, tags_rows, developer_rows, publisher_rows, category_rows


def insert_game_sources(connection: sqlite3.Connection, audits: list[dict[str, Any]]) -> tuple[int, int]:
    snapshots = 0
    errors = 0
    snapshot_columns = [
        "appid", "snapshot_date", "snapshot_label", "source_dataset", "source_file", "name", "release_date", "release_year",
        "required_age", "price", "discount", "dlc_count", "is_free", "estimated_owners", "owner_low", "owner_high", "owner_mid",
        "peak_ccu", "positive", "negative", "total_reviews", "positive_share", "wilson_lower", "pct_pos_recent", "num_reviews_recent",
        "average_playtime_forever", "average_playtime_2weeks", "median_playtime_forever", "median_playtime_2weeks", "user_score",
        "metacritic_score", "windows", "mac", "linux", "developers_json", "publishers_json", "categories_json", "genres_json", "tags_json",
        "raw_header_fields", "row_field_count", "alignment_repaired",
    ]
    placeholders = ",".join("?" for _ in snapshot_columns)
    insert_sql = f"INSERT OR REPLACE INTO game_snapshot ({','.join(snapshot_columns)}) VALUES ({placeholders})"
    bridge_buffers = {"genre": [], "tag": [], "developer": [], "publisher": [], "category": []}
    for spec, audit in zip(SOURCES, audits):
        path = Path(spec["path"])
        handle, reader, raw_header, header, _ = open_csv(path)
        try:
            batch: list[tuple[Any, ...]] = []
            for row in reader:
                if len(row) != len(header):
                    errors += 1
                    continue
                try:
                    record, genres, tags, developers, publishers, categories = parse_game_row(row, header, spec, len(raw_header))
                except ValueError:
                    errors += 1
                    continue
                batch.append(tuple(record[column] for column in snapshot_columns))
                bridge_buffers["genre"].extend(genres)
                bridge_buffers["tag"].extend(tags)
                bridge_buffers["developer"].extend(developers)
                bridge_buffers["publisher"].extend(publishers)
                bridge_buffers["category"].extend(categories)
                if len(batch) >= 5000:
                    connection.executemany(insert_sql, batch)
                    connection.executemany("INSERT OR IGNORE INTO game_genre VALUES (?,?,?)", bridge_buffers["genre"])
                    connection.executemany("INSERT OR IGNORE INTO game_tag VALUES (?,?,?,?)", bridge_buffers["tag"])
                    connection.executemany("INSERT OR IGNORE INTO game_developer VALUES (?,?,?)", bridge_buffers["developer"])
                    connection.executemany("INSERT OR IGNORE INTO game_publisher VALUES (?,?,?)", bridge_buffers["publisher"])
                    connection.executemany("INSERT OR IGNORE INTO game_category VALUES (?,?,?)", bridge_buffers["category"])
                    connection.commit()
                    batch.clear()
                    for items in bridge_buffers.values():
                        items.clear()
                snapshots += 1
            if batch:
                connection.executemany(insert_sql, batch)
                connection.executemany("INSERT OR IGNORE INTO game_genre VALUES (?,?,?)", bridge_buffers["genre"])
                connection.executemany("INSERT OR IGNORE INTO game_tag VALUES (?,?,?,?)", bridge_buffers["tag"])
                connection.executemany("INSERT OR IGNORE INTO game_developer VALUES (?,?,?)", bridge_buffers["developer"])
                connection.executemany("INSERT OR IGNORE INTO game_publisher VALUES (?,?,?)", bridge_buffers["publisher"])
                connection.executemany("INSERT OR IGNORE INTO game_category VALUES (?,?,?)", bridge_buffers["category"])
                connection.commit()
                batch.clear()
                for items in bridge_buffers.values():
                    items.clear()
        finally:
            handle.close()
        connection.execute(
            "INSERT INTO source_manifest VALUES (?,?,?,?,?,?,?,?,?,?)",
            (
                str(spec["source_file"]), str(spec["dataset"]), str(spec["snapshot_date"]), str(spec["snapshot_label"]),
                int(audit["bytes"]), int(audit["rows"]), int(audit["normalised_header_fields"]), str(audit["header_fix"]),
                "MIT", "Raw file left untouched; current games.csv header repaired only in normalized layer.",
            ),
        )
    return snapshots, errors


def build_dim_game(connection: sqlite3.Connection) -> None:
    connection.execute(
        """
        INSERT INTO dim_game
        SELECT appid, name, release_date, release_year,
               MIN(snapshot_date), MAX(snapshot_date), COUNT(*), COUNT(DISTINCT source_dataset),
               developers_json, publishers_json, genres_json, tags_json
        FROM (
            SELECT *, ROW_NUMBER() OVER (PARTITION BY appid ORDER BY snapshot_date DESC, source_file DESC) AS rn
            FROM game_snapshot
        )
        WHERE rn = 1
        GROUP BY appid, name, release_date, release_year, developers_json, publishers_json, genres_json, tags_json
        """
    )
    connection.execute(
        """
        UPDATE dim_game
        SET first_seen_snapshot = (SELECT MIN(snapshot_date) FROM game_snapshot WHERE appid = dim_game.appid),
            last_seen_snapshot = (SELECT MAX(snapshot_date) FROM game_snapshot WHERE appid = dim_game.appid),
            snapshot_count = (SELECT COUNT(*) FROM game_snapshot WHERE appid = dim_game.appid),
            source_count = (SELECT COUNT(DISTINCT source_dataset) FROM game_snapshot WHERE appid = dim_game.appid)
        """
    )
    connection.commit()


def update_quality_table(connection: sqlite3.Connection, audits: list[dict[str, Any]], zip_audits: list[dict[str, Any]]) -> None:
    for audit in audits + zip_audits:
        source = str(audit["source_file"])
        for key, value_ in audit.items():
            if key in {"source_file", "normalised_header", "expected_header", "header"}:
                continue
            status = audit.get("status", "review")
            connection.execute(
                "INSERT INTO data_quality_check VALUES (?,?,?,?,?)",
                (source, key, json.dumps(value_, ensure_ascii=False), status, "Automated structural/semantic audit"),
            )
    connection.commit()


def aggregate_reviews(connection: sqlite3.Connection) -> dict[str, Any]:
    if not REVIEW_ZIP.exists():
        return {"status": "missing", "rows": 0}
    game: dict[int, list[Any]] = {}
    month: dict[tuple[int, str], list[Any]] = {}
    rows = 0
    bad = 0
    with zipfile.ZipFile(REVIEW_ZIP) as archive, archive.open(REVIEW_FILE) as raw:
        text = io.TextIOWrapper(raw, encoding="utf-8-sig", errors="replace", newline="")
        reader = csv.reader(text)
        header = [clean_name(item) for item in next(reader)]
        for row in reader:
            rows += 1
            if len(row) != len(header):
                bad += 1
                continue
            values = dict(zip(header, row))
            appid = safe_int(values.get("appid"))
            created = safe_int(values.get("timestamp_created"))
            if appid is None or created is None:
                bad += 1
                continue
            date = datetime.fromtimestamp(created, tz=timezone.utc).date().isoformat()
            year_month = date[:7]
            positive = 1 if str(values.get("voted_up", "")).lower() in {"true", "1"} else 0
            playtime_hours = (safe_float(values.get("author_playtime_at_review")) or 0.0) / 60.0
            helpful = safe_float(values.get("votes_up")) or 0.0
            purchased = 1 if str(values.get("steam_purchase", "")).lower() in {"true", "1"} else 0
            free_received = 1 if str(values.get("received_for_free", "")).lower() in {"true", "1"} else 0
            early = 1 if str(values.get("written_during_early_access", "")).lower() in {"true", "1"} else 0
            for target, key in ((game, appid), (month, (appid, year_month))):
                if key not in target:
                    target[key] = [0, 0, 0, 0.0, 0.0, 0, 0, 0, set(), date, date]
                acc = target[key]
                acc[0] += 1
                acc[1] += positive
                acc[2] += 1 - positive
                acc[3] += playtime_hours
                acc[4] += helpful
                acc[5] += purchased
                acc[6] += free_received
                acc[7] += early
                language = values.get("language") or "unknown"
                acc[8].add(language)
                acc[9] = min(acc[9], date)
                acc[10] = max(acc[10], date)
    for appid, acc in game.items():
        total = acc[0]
        connection.execute(
            "INSERT OR REPLACE INTO review_game VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (appid, total, acc[1], acc[2], acc[1] / total if total else None, acc[3] / total / 1.0,
             acc[4] / total, acc[5] / total, acc[6] / total, acc[7] / total, len(acc[8]), acc[9], acc[10],
             "weighted_score_above_08 only"),
        )
    for (appid, year_month), acc in month.items():
        total = acc[0]
        connection.execute(
            "INSERT OR REPLACE INTO review_game_month VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            (appid, year_month, total, acc[1], acc[2], acc[1] / total if total else None, acc[3] / total,
             acc[4] / total, acc[5] / total, acc[7] / total, "weighted_score_above_08 only"),
        )
    connection.commit()
    return {"status": "pass", "rows": rows, "bad_rows": bad, "games": len(game), "game_months": len(month), "filter": "weighted_score_above_08"}


def aggregate_recommendations(connection: sqlite3.Connection) -> dict[str, Any]:
    if not RECOMMENDATION_ZIP.exists():
        return {"status": "missing", "rows": 0}
    game: dict[int, list[Any]] = {}
    month: dict[tuple[int, str], list[Any]] = {}
    rows = 0
    bad = 0
    with zipfile.ZipFile(RECOMMENDATION_ZIP) as archive, archive.open(RECOMMENDATION_FILE) as raw:
        text = io.TextIOWrapper(raw, encoding="utf-8-sig", errors="replace", newline="")
        reader = csv.reader(text)
        header = [clean_name(item) for item in next(reader)]
        for row in reader:
            rows += 1
            if len(row) != len(header):
                bad += 1
                continue
            values = dict(zip(header, row))
            appid = safe_int(values.get("app_id"))
            date = str(values.get("date") or "")[:10]
            if appid is None or not re.fullmatch(r"\d{4}-\d{2}-\d{2}", date):
                bad += 1
                continue
            positive = 1 if str(values.get("is_recommended", "")).lower() in {"true", "1"} else 0
            hours = safe_float(values.get("hours")) or 0.0
            helpful = safe_float(values.get("helpful")) or 0.0
            funny = safe_float(values.get("funny")) or 0.0
            year_month = date[:7]
            for target, key in ((game, appid), (month, (appid, year_month))):
                if key not in target:
                    target[key] = [0, 0, 0, 0.0, 0.0, 0.0, date, date]
                acc = target[key]
                acc[0] += 1
                acc[1] += positive
                acc[2] += 1 - positive
                acc[3] += hours
                acc[4] += helpful
                acc[5] += funny
                acc[6] = min(acc[6], date)
                acc[7] = max(acc[7], date)
    for appid, acc in game.items():
        total = acc[0]
        connection.execute(
            "INSERT OR REPLACE INTO recommendation_game VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            (appid, total, acc[1], acc[2], acc[1] / total if total else None, None, acc[3] / total,
             acc[4] / total, acc[5] / total, acc[6], acc[7]),
        )
    for (appid, year_month), acc in month.items():
        total = acc[0]
        connection.execute(
            "INSERT OR REPLACE INTO recommendation_game_month VALUES (?,?,?,?,?,?,?,?,?)",
            (appid, year_month, total, acc[1], acc[2], acc[1] / total if total else None, acc[3] / total,
             acc[4] / total, acc[5] / total),
        )
    connection.commit()
    return {"status": "pass", "rows": rows, "bad_rows": bad, "games": len(game), "game_months": len(month)}


def numeric_median(connection: sqlite3.Connection, query: str, params: tuple[Any, ...]) -> float | None:
    values = [row[0] for row in connection.execute(query, params) if row[0] is not None]
    if not values:
        return None
    values.sort()
    middle = len(values) // 2
    return float(values[middle]) if len(values) % 2 else (values[middle - 1] + values[middle]) / 2.0


def build_analysis_tables(connection: sqlite3.Connection) -> None:
    snapshots = [row[0] for row in connection.execute("SELECT DISTINCT snapshot_date FROM game_snapshot ORDER BY snapshot_date")]
    years = [row[0] for row in connection.execute("SELECT DISTINCT release_year FROM game_snapshot WHERE release_year IS NOT NULL ORDER BY release_year")]
    for snapshot_date in snapshots:
        for release_year in years:
            count = connection.execute("SELECT COUNT(*) FROM game_snapshot WHERE snapshot_date=? AND release_year=?", (snapshot_date, release_year)).fetchone()[0]
            if not count:
                continue
            free_share = connection.execute("SELECT AVG(is_free) FROM game_snapshot WHERE snapshot_date=? AND release_year=?", (snapshot_date, release_year)).fetchone()[0]
            median_price = numeric_median(connection, "SELECT price FROM game_snapshot WHERE snapshot_date=? AND release_year=?", (snapshot_date, release_year))
            median_reviews = numeric_median(connection, "SELECT total_reviews FROM game_snapshot WHERE snapshot_date=? AND release_year=?", (snapshot_date, release_year))
            median_peak = numeric_median(connection, "SELECT peak_ccu FROM game_snapshot WHERE snapshot_date=? AND release_year=?", (snapshot_date, release_year))
            median_positive = numeric_median(connection, "SELECT positive_share FROM game_snapshot WHERE snapshot_date=? AND release_year=?", (snapshot_date, release_year))
            connection.execute("INSERT OR REPLACE INTO market_year_summary VALUES (?,?,?,?,?,?,?,?)", (snapshot_date, release_year, count, free_share, median_price, median_reviews, median_peak, median_positive))
    connection.execute(
        """
        INSERT OR REPLACE INTO game_snapshot_comparison
        SELECT appid,
          MAX(CASE WHEN snapshot_label='may_2024' THEN name END),
          MAX(CASE WHEN snapshot_label='march_2025' THEN name END),
          MAX(CASE WHEN snapshot_label='current_2026' THEN name END),
          MAX(CASE WHEN snapshot_label='may_2024' THEN release_date END),
          MAX(CASE WHEN snapshot_label='march_2025' THEN release_date END),
          MAX(CASE WHEN snapshot_label='current_2026' THEN release_date END),
          MAX(CASE WHEN snapshot_label='may_2024' THEN total_reviews END),
          MAX(CASE WHEN snapshot_label='march_2025' THEN total_reviews END),
          MAX(CASE WHEN snapshot_label='current_2026' THEN total_reviews END),
          MAX(CASE WHEN snapshot_label='may_2024' THEN peak_ccu END),
          MAX(CASE WHEN snapshot_label='march_2025' THEN peak_ccu END),
          MAX(CASE WHEN snapshot_label='current_2026' THEN peak_ccu END),
          MAX(CASE WHEN snapshot_label='may_2024' THEN price END),
          MAX(CASE WHEN snapshot_label='march_2025' THEN price END),
          MAX(CASE WHEN snapshot_label='current_2026' THEN price END),
          MAX(CASE WHEN snapshot_label='may_2024' THEN owner_mid END),
          MAX(CASE WHEN snapshot_label='march_2025' THEN owner_mid END),
          MAX(CASE WHEN snapshot_label='current_2026' THEN owner_mid END),
          CASE WHEN COUNT(CASE WHEN snapshot_label='may_2024' THEN 1 END)>0 AND COUNT(CASE WHEN snapshot_label='march_2025' THEN 1 END)>0 THEN 'continued' WHEN COUNT(CASE WHEN snapshot_label='may_2024' THEN 1 END)>0 THEN 'only_2024' ELSE 'only_2025' END,
          CASE WHEN COUNT(CASE WHEN snapshot_label='march_2025' THEN 1 END)>0 AND COUNT(CASE WHEN snapshot_label='current_2026' THEN 1 END)>0 THEN 'continued' WHEN COUNT(CASE WHEN snapshot_label='march_2025' THEN 1 END)>0 THEN 'only_2025' ELSE 'only_2026' END,
          CASE WHEN MAX(CASE WHEN snapshot_label='may_2024' THEN total_reviews END)>0 THEN CAST(MAX(CASE WHEN snapshot_label='march_2025' THEN total_reviews END) AS REAL) / MAX(CASE WHEN snapshot_label='may_2024' THEN total_reviews END) - 1 END,
          CASE WHEN MAX(CASE WHEN snapshot_label='march_2025' THEN total_reviews END)>0 THEN CAST(MAX(CASE WHEN snapshot_label='current_2026' THEN total_reviews END) AS REAL) / MAX(CASE WHEN snapshot_label='march_2025' THEN total_reviews END) - 1 END
        FROM game_snapshot
        GROUP BY appid
        """
    )
    connection.commit()


def build_join_coverage(connection: sqlite3.Connection) -> None:
    connection.execute("DELETE FROM join_coverage")
    for table in ("review_game", "recommendation_game"):
        fact_rows = connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
        distinct_appids = connection.execute(f"SELECT COUNT(DISTINCT appid) FROM {table}").fetchone()[0]
        matched_appids = connection.execute(
            f"SELECT COUNT(*) FROM {table} f INNER JOIN dim_game g ON g.appid=f.appid"
        ).fetchone()[0]
        connection.execute(
            "INSERT INTO join_coverage VALUES (?,?,?,?,?)",
            (table, fact_rows, distinct_appids, matched_appids, matched_appids / distinct_appids if distinct_appids else None),
        )
    connection.commit()


def write_summary(output: Path, connection: sqlite3.Connection, audits: list[dict[str, Any]], review_summary: dict[str, Any], recommendation_summary: dict[str, Any], errors: int) -> None:
    summary = {
        "database": str(DB_PATH),
        "sources": [audit["source_file"] for audit in audits],
        "game_audits": audits,
        "review_summary": review_summary,
        "recommendation_summary": recommendation_summary,
        "game_rows_inserted": connection.execute("SELECT COUNT(*) FROM game_snapshot").fetchone()[0],
        "unique_games": connection.execute("SELECT COUNT(*) FROM dim_game").fetchone()[0],
        "game_row_errors": errors,
        "tables": {row[0]: connection.execute(f"SELECT COUNT(*) FROM {row[0]}").fetchone()[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")},
    }
    (output / "build_summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")


def main() -> None:
    OUTPUT.mkdir(parents=True, exist_ok=True)
    audits = [audit_game_file(spec) for spec in SOURCES]
    zip_audits = [
        audit_zip(REVIEW_ZIP, REVIEW_FILE, [
            "recommendationid", "appid", "game", "author_steamid", "author_num_games_owned", "author_num_reviews",
            "author_playtime_forever", "author_playtime_last_two_weeks", "author_playtime_at_review", "author_last_played",
            "language", "review", "timestamp_created", "timestamp_updated", "voted_up", "votes_up", "votes_funny",
            "weighted_vote_score", "comment_count", "steam_purchase", "received_for_free", "written_during_early_access",
            "hidden_in_steam_china", "steam_china_location",
        ]) if REVIEW_ZIP.exists() else {"source_file": REVIEW_ZIP.name, "status": "missing"},
        audit_zip(RECOMMENDATION_ZIP, RECOMMENDATION_FILE, ["app_id", "helpful", "funny", "date", "is_recommended", "hours", "user_id", "review_id"])
        if RECOMMENDATION_ZIP.exists() else {"source_file": RECOMMENDATION_ZIP.name, "status": "missing"},
    ]
    if DB_PATH.exists():
        DB_PATH.unlink()
    connection = sqlite3.connect(DB_PATH)
    try:
        create_schema(connection)
        update_quality_table(connection, audits, zip_audits)
        _, errors = insert_game_sources(connection, audits)
        build_dim_game(connection)
        review_summary = aggregate_reviews(connection)
        recommendation_summary = aggregate_recommendations(connection)
        build_join_coverage(connection)
        build_analysis_tables(connection)
        write_summary(OUTPUT, connection, audits, review_summary, recommendation_summary, errors)
        connection.execute("VACUUM")
    finally:
        connection.close()


if __name__ == "__main__":
    main()
