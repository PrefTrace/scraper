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
    parse_achievement_schema,
    parse_app_details,
    parse_appinfo_semantics,
    parse_bundle_membership,
    parse_external_links,
    parse_global_achievement_percentages,
    parse_media,
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
            if actual is None or actual.usb is not wanted["usb"] or actual.bluetooth is not wanted[
                "bluetooth"
            ]:
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
