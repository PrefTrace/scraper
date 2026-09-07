"""Snapshot-based positive controls for the Steam TZ mappings.

The controls deliberately start with source-shaped raw payloads and assert the
normalized semantic result.  They are separate from the ORM contract audit so
that a non-null column cannot make a broken parser look healthy.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .locales import normalize_locale
from .parsers import (
    normalize_descriptor_text,
    parse_achievement_schema,
    parse_app_details,
    parse_appinfo_semantics,
    parse_build_branches,
    parse_bundle_membership,
    parse_country_restrictions,
    parse_descriptors,
    parse_external_links,
    parse_global_achievement_percentages,
    parse_media,
    parse_organizations,
    parse_store_browse_item,
    parse_structured_genres,
    parse_structured_tags,
    parse_workshop_stats,
)

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_MANIFEST = PROJECT_ROOT / "demo" / "steam_positive_controls.json"


def _run_case(case: dict[str, Any], manifest_path: Path) -> tuple[bool, str]:
    kind = case.get("kind")
    raw = case.get("raw")
    expected = case.get("expected") or {}

    if kind == "appinfo":
        parsed = parse_appinfo_semantics(raw if isinstance(raw, dict) else {})
        if "vac_enabled" in expected and parsed["vac_enabled"] != expected["vac_enabled"]:
            return False, f"vac_enabled={parsed['vac_enabled']!r}"
        if (
            "gamepad_preferred" in expected
            and parsed["gamepad_preferred"] != expected["gamepad_preferred"]
        ):
            return False, f"gamepad_preferred={parsed['gamepad_preferred']!r}"
        if (
            "controller_support" in expected
            and parsed["controller_support"] != expected["controller_support"]
        ):
            return False, f"controller_support={parsed['controller_support']!r}"
        if "controller" in expected:
            wanted = expected["controller"]
            controllers = {item.name: item for item in parsed["controllers"]}
            actual = controllers.get(wanted["name"])
            if (
                actual is None
                or actual.usb is not wanted["usb"]
                or actual.bluetooth is not wanted["bluetooth"]
            ):
                return False, f"controllers={controllers!r}"
        if "accessibility_ids" in expected:
            actual_ids = sorted(item.id for item in parsed["accessibility_features"])
            if actual_ids != expected["accessibility_ids"]:
                return False, f"accessibility_ids={actual_ids!r}"
        return True, "raw appinfo semantics matched"

    if kind == "details":
        parsed = parse_app_details(raw if isinstance(raw, dict) else {}, normalize_locale("en-US"))
        for key, wanted in expected.items():
            if key == "eula_url":
                actual = parsed["eulas"][0].url if parsed["eulas"] else None
            elif key == "rating_minimum_age":
                actual = next(
                    (
                        item.minimum_age
                        for item in parsed["age_ratings"]
                        if item.authority.casefold() == "pegi"
                    ),
                    None,
                )
            else:
                actual = parsed.get(key)
            if actual != wanted:
                return False, f"{key}={actual!r}, expected={wanted!r}"
        return True, "raw appdetails semantics matched"

    if kind == "links":
        links = parse_external_links(raw if isinstance(raw, dict) else {})
        actual_types = sorted({item.type for item in links})
        expected_types = sorted(expected.get("types", []))
        if actual_types != expected_types:
            return False, f"types={actual_types!r}, expected={expected_types!r}"
        return True, "structured links normalized"

    if kind == "media":
        media = parse_media(raw if isinstance(raw, dict) else {})
        actual_types = [item.media_type for item in media]
        expected_types = expected.get("types", [])
        if sorted(set(actual_types)) != sorted(expected_types):
            return False, f"types={actual_types!r}, expected={expected_types!r}"
        if actual_types.count("main_capsule") != expected.get("main_capsule_count"):
            return False, f"main_capsule_count={actual_types.count('main_capsule')}"
        trailer = next((item for item in media if item.media_type == "trailer"), None)
        if trailer is None or trailer.format != expected.get("trailer_format"):
            return False, f"trailer={trailer!r}"
        return True, "raw assets mapped to canonical media"

    if kind == "achievements":
        paths = case.get("source_files") or {}
        schema_path = (manifest_path.parent / paths["schema"]).resolve()
        percentages_path = (manifest_path.parent / paths["percentages"]).resolve()
        schema = json.loads(schema_path.read_text(encoding="utf-8"))
        percentages = json.loads(percentages_path.read_text(encoding="utf-8"))
        achievements = parse_achievement_schema(
            schema,
            language="en-US",
            percentages=parse_global_achievement_percentages(percentages),
        )
        ids = [item.achievement_id for item in achievements]
        values = [item.global_percent for item in achievements]
        if ids != expected.get("ids") or values != expected.get("percentages"):
            return False, f"ids={ids!r}, percentages={values!r}"
        return True, "structured achievement IDs and percentages matched"

    if kind == "bundle":
        bundle_id, package_ids = parse_bundle_membership(raw if isinstance(raw, dict) else {})
        if bundle_id != expected.get("bundle_id") or package_ids != expected.get("package_ids"):
            return False, f"bundle={bundle_id!r}, packages={package_ids!r}"
        return True, "bundle-specific membership matched"

    if kind == "price":
        parsed = parse_store_browse_item(raw if isinstance(raw, dict) else {})
        prices = {item.package_id: item for item in parsed.get("edition_prices", [])}
        package_id = int(expected["package_id"])
        price = prices.get(package_id)
        if price is None:
            return False, f"package {package_id} missing"
        actual = {
            "initial": price.initial,
            "final": price.final,
            "discount_percent": price.discount_percent,
            "price_region": price.price_region,
        }
        wanted = {key: expected[key] for key in actual if key in expected}
        if any(actual[key] != value for key, value in wanted.items()):
            return False, f"price={actual!r}, expected={wanted!r}"
        return True, "regional price fields matched"

    if kind == "descriptor":
        values = parse_descriptors(raw if isinstance(raw, dict) else {})
        actual = sorted(item.name for item in values if item.name)
        wanted = sorted(expected.get("names", []))
        normalized = sorted(
            {
                fragment
                for name in actual
                for fragment in normalize_descriptor_text(name, language="en")
            }
        )
        if not set(wanted).issubset(set(normalized)):
            return False, f"normalized={normalized!r}, expected={wanted!r}"
        return True, "descriptor normalizer matched"

    if kind == "depot":
        branches = parse_build_branches(raw if isinstance(raw, dict) else {})
        branch = next((item for item in branches if item.name == expected.get("branch")), None)
        if branch is None:
            return False, "branch missing"
        if branch.download_size_min != expected.get("download_size_min"):
            return False, f"download_size_min={branch.download_size_min!r}"
        return True, "unknown depot sizes stayed unknown"

    if kind == "tag":
        tags, localizations = parse_structured_tags(raw if isinstance(raw, dict) else {})
        if not tags or tags[0].tag_id != expected.get("tag_id"):
            return False, f"tags={tags!r}"
        if tags[0].weight != expected.get("weight"):
            return False, f"weight={tags[0].weight!r}"
        if not any(
            item.tag_id == expected.get("tag_id")
            and item.language == expected.get("language")
            and item.name == expected.get("name")
            for item in localizations
        ):
            return False, f"localizations={localizations!r}"
        return True, "tag identity, weight, and localization matched"

    if kind == "genre":
        genres, localizations = parse_structured_genres(
            raw if isinstance(raw, dict) else {}, language=expected.get("language", "en")
        )
        if not genres or genres[0].genre_id != expected.get("genre_id"):
            return False, f"genres={genres!r}"
        if not any(
            item.genre_id == expected.get("genre_id")
            and item.language == expected.get("language")
            and item.name == expected.get("name")
            for item in localizations
        ):
            return False, f"localizations={localizations!r}"
        return True, "genre identity and localization matched"

    if kind == "organization":
        credits = parse_organizations(raw if isinstance(raw, dict) else {})
        wanted = expected.get("credit") or {}
        if not any(
            item.status == wanted.get("status")
            and item.creator_clan_account_id == wanted.get("creator_clan_account_id")
            and item.credited_name == wanted.get("credited_name")
            for item in credits
        ):
            return False, f"credits={credits!r}"
        return True, "creator identity and credited_name matched"

    if kind == "restriction":
        restrictions = parse_country_restrictions(raw if isinstance(raw, dict) else {})
        wanted = expected.get("restriction") or {}
        if not any(
            item.package_id == wanted.get("package_id")
            and item.restriction_type == wanted.get("restriction_type")
            and item.country_code == wanted.get("country_code")
            for item in restrictions
        ):
            return False, f"restrictions={restrictions!r}"
        return True, "country restriction stayed separate from regional price"

    if kind == "workshop":
        stats = parse_workshop_stats(
            raw if isinstance(raw, dict) else {},
            case.get("html"),
            collection_html=case.get("collection_html"),
            app_id=1,
        )
        for key in ("workshop_available", "published_file_count", "collection_count"):
            if getattr(stats, key) != expected.get(key):
                return False, f"{key}={getattr(stats, key)!r}"
        return True, "anonymous Workshop totals matched"

    return False, f"unknown control kind {kind!r}"


def run_positive_controls(manifest_path: Path = DEFAULT_MANIFEST) -> dict[str, Any]:
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    controls = manifest.get("controls", [])
    results: list[dict[str, Any]] = []
    for case in controls:
        try:
            passed, message = _run_case(case, manifest_path)
        except Exception as exc:  # pragma: no cover - reported as a benchmark result
            passed, message = False, f"{type(exc).__name__}: {exc}"
        results.append({"name": case.get("name"), "ok": passed, "message": message})
    return {
        "ok": bool(results) and all(item["ok"] for item in results),
        "total": len(results),
        "passed": sum(1 for item in results if item["ok"]),
        "failed": [item for item in results if not item["ok"]],
        "control_app_ids": manifest.get("control_app_ids", []),
    }


__all__ = ["run_positive_controls"]
