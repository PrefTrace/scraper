"""Small, defensive parsers for the Steam Store and community responses.

Steam has no single stable schema for all of the data exposed on an app page.
The functions in this module keep the source vocabulary visible and avoid
throwing away fields just because a particular app does not have them.
"""

from __future__ import annotations

import calendar
import re
import statistics
import unicodedata
from datetime import UTC, date, datetime
from typing import Any
from urllib.parse import parse_qs, unquote, urlparse, urlsplit, urlunsplit

from selectolax.parser import HTMLParser, Node

try:
    from simplemma import lemma as _simplemma_lemma
except ImportError:  # pragma: no cover - dependency is declared in requirements.txt
    _simplemma_lemma = None

try:
    import stopwordsiso as _stopwordsiso
except ImportError:  # pragma: no cover - dependency is declared in requirements.txt
    _stopwordsiso = None

from scraper.models import (
    Achievement,
    AgeRating,
    AppRelationship,
    BuildBranch,
    Bundle,
    BundlePrice,
    Category,
    Controller,
    Descriptor,
    EditionInfo,
    EditionPrice,
    ExternalLink,
    ExternalReview,
    Feature,
    Genre,
    GenreLocalization,
    LanguageSupport,
    LocalizedGameInfo,
    MediaImage,
    MediaVideo,
    OrganizationCredit,
    PriceOverview,
    Requirements,
    RequirementsByOs,
    ReviewLanguageStats,
    SteamDeckSupport,
    SystemRequirement,
    Tag,
    TagLocalization,
    TextValue,
    ThirdPartyEula,
    WorkshopStats,
)

from .locales import (
    LocaleInfo,
    canonicalize_language_name,
    steam_code_to_bcp47,
    steam_name_to_code,
)

_CONTENT_DESCRIPTOR_NAMES = {
    1: "Some Nudity or Sexual Content",
    2: "Frequent Violence or Gore",
    3: "Adult Only Sexual Content",
    4: "Frequent Nudity or Sexual Content",
    5: "General Mature Content",
}

_DESCRIPTOR_METADATA = re.compile(
    r"(?:certificate|registration|company\s+name|title\s+name|rating\s+number|serial\s+number)",
    re.IGNORECASE,
)
_DESCRIPTOR_STOPWORDS = {
    "a",
    "an",
    "and",
    "as",
    "contains",
    "contain",
    "includes",
    "of",
    "or",
    "the",
    "with",
}
_DESCRIPTOR_NEGATIONS = {"no", "not", "without", "nicht", "ne", "нет", "без"}

_MEDIA_KEY_TYPES = {
    "header_image": "header_capsule",
    "capsule_image": "main_capsule",
    "capsule_imagev5": "small_capsule",
    "capsule_imagev6": "main_capsule",
    "small_capsule": "small_capsule",
    "vertical_capsule": "vertical_capsule",
    "background": "page_background",
    "background_raw": "page_background",
}

# The three legacy ``capsule_image*`` fields are alternate resolutions of the
# same store Main Capsule, not three domain media classes.  Prefer the newest
# field when it exists and emit one canonical row for that source asset class.
_LEGACY_ASSET_PRIORITY = (
    "capsule_imagev6",
    "capsule_imagev5",
    "capsule_image",
)

_STORE_ASSET_TYPES = {
    "header": "header_capsule",
    "main_capsule": "main_capsule",
    "small_capsule": "small_capsule",
    "vertical_capsule": "vertical_capsule",
    "page_background": "page_background",
    "library_capsule": "library_capsule",
    "library_hero": "library_hero",
    "library_header": "library_header",
    "library_logo": "library_logo",
}

_CANONICAL_MEDIA_TYPES = {
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


def _canonical_media_type(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    normalized = re.sub(r"[^a-z0-9]+", "_", value.casefold()).strip("_")
    normalized = {
        "header": "header_capsule",
        "header_image": "header_capsule",
        "capsule": "main_capsule",
        "capsule_image": "main_capsule",
        "capsule_imagev5": "small_capsule",
        "capsule_imagev6": "main_capsule",
        "background": "page_background",
        "background_raw": "page_background",
        "movie": "trailer",
        "video": "trailer",
    }.get(normalized, normalized)
    return normalized if normalized in _CANONICAL_MEDIA_TYPES else None


_STEAM_TYPE_MAP = {
    "game": "game",
    "dlc": "dlc",
    "music": "soundtrack",
    "soundtrack": "soundtrack",
    "software": "application",
    "application": "application",
}

_STEAM_CATEGORY_NAMES = {
    1: "Multi-player",
    2: "Single-player",
    3: "Co-op",
    8: "Valve Anti-Cheat enabled",
    18: "Partial Controller Support",
    22: "Steam Achievements",
    23: "Steam Cloud",
    27: "Cross-Platform Multiplayer",
    28: "Full Controller Support",
    29: "Steam Trading Cards",
    30: "Steam Workshop",
    33: "Steam Workshop",
    45: "Remote Play on Phone",
    46: "Remote Play on Tablet",
    55: "DualShock 4 (USB)",
    56: "DualShock 4 (Bluetooth)",
    57: "DualSense (USB)",
    58: "DualSense (Bluetooth)",
    60: "Gamepad Preferred",
    62: "Family Sharing",
}


def normalize_steam_type(value: Any) -> str | None:
    """Map Steam's display type to the TZ canonical vocabulary."""

    if not isinstance(value, str) or not value.strip():
        return None
    normalized = value.strip().casefold()
    return _STEAM_TYPE_MAP.get(normalized)


def _node_text(node: Node | None) -> str:
    if node is None:
        return ""
    return re.sub(r"\s+", " ", node.text(separator=" ", strip=True)).strip()


def plain_text(value: str | None) -> str:
    if not value:
        return ""
    parser = HTMLParser(f"<div>{value}</div>")
    return _node_text(parser.css_first("div"))


def text_value(value: str | None) -> TextValue | None:
    if value is None:
        return None
    return TextValue(text=plain_text(value), html=value)


def _optional_url(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    value = value.strip()
    if value.startswith("//"):
        value = f"https:{value}"
    if not value.startswith(("http://", "https://")):
        return None
    parsed = urlsplit(value)
    path = re.sub(r"/{2,}", "/", parsed.path)
    return str(urlunsplit((parsed.scheme, parsed.netloc, path, parsed.query, parsed.fragment)))


def _parse_int(value: Any) -> int | None:
    if isinstance(value, bool) or value in (None, ""):
        return None
    try:
        return int(value)
    except (TypeError, ValueError, OverflowError):
        return None


def _parse_float(value: Any) -> float | None:
    if isinstance(value, bool) or value in (None, ""):
        return None
    try:
        return float(value)
    except (TypeError, ValueError, OverflowError):
        return None


def _parse_datetime(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        return value.replace(tzinfo=None)
    if isinstance(value, (int, float)) and value > 0:
        return datetime.fromtimestamp(value, tz=UTC).replace(tzinfo=None)
    if isinstance(value, str) and value.strip():
        raw = value.strip().replace("Z", "+00:00")
        try:
            parsed = datetime.fromisoformat(raw)
        except ValueError:
            return None
        return parsed.astimezone(UTC).replace(tzinfo=None) if parsed.tzinfo else parsed
    return None


def _discount_metadata(raw: dict[str, Any]) -> tuple[str | None, datetime | None]:
    discounts = raw.get("active_discounts")
    values = list(discounts.values()) if isinstance(discounts, dict) else _as_list(discounts)
    if not values:
        values = [raw]
    for item in values:
        if not isinstance(item, dict):
            continue
        description = item.get("description") or item.get("discount_description")
        end_at = _parse_datetime(
            item.get("end_at", item.get("timestamp_end", item.get("end_time")))
        )
        if isinstance(description, str) and description.strip() or end_at is not None:
            return (
                description.strip()
                if isinstance(description, str) and description.strip()
                else None,
                end_at,
            )
    return None, None


def _price_region(raw: dict[str, Any]) -> str | None:
    for key in ("price_region", "price_region_code", "steam_price_region"):
        value = raw.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip().upper()
    return None


def _discount_from_prices(
    initial: int | None, final: int | None, reported: int | None
) -> tuple[int | None, str | None]:
    if initial is None or final is None or reported is None or initial <= 0:
        return reported, None
    calculated = round((initial - final) * 100 / initial)
    if abs(calculated - reported) > 1:
        return reported, (
            f"Price discount is inconsistent: initial={initial}, final={final}, "
            f"reported={reported}, calculated={calculated}"
        )
    return reported, None


def _parse_bool(value: Any) -> bool | None:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    if isinstance(value, str):
        normalized = value.strip().casefold()
        if normalized in {"1", "true", "yes", "on"}:
            return True
        if normalized in {"0", "false", "no", "off"}:
            return False
    return None


def _field(value: Any, name: str, default: Any = None) -> Any:
    if isinstance(value, dict):
        return value.get(name, default)
    return getattr(value, name, default)


def _as_list(value: Any) -> list[Any]:
    if isinstance(value, list):
        return value
    if isinstance(value, tuple):
        return list(value)
    return []


def _format_from_url(value: str | None) -> str | None:
    if not value:
        return None
    match = re.search(r"\.([a-z0-9]{2,5})(?:[?#].*)?$", value.casefold())
    return match.group(1) if match else None


def _link_type(value: str) -> str:
    normalized = value.strip().casefold()
    if normalized in {"support_website", "support_email", "website", "external"}:
        return normalized
    return re.sub(r"[^a-z0-9]+", "", normalized)


def _social_link_type(url: str, label: str = "") -> str:
    host = (urlparse(url).hostname or "").casefold().removeprefix("www.")
    known_hosts = (
        ("facebook.com", "facebook"),
        ("twitter.com", "twitter"),
        ("x.com", "twitter"),
        ("discord.com", "discord"),
        ("discord.gg", "discord"),
        ("youtube.com", "youtube"),
        ("youtu.be", "youtube"),
        ("twitch.tv", "twitch"),
        ("instagram.com", "instagram"),
        ("reddit.com", "reddit"),
        ("vk.com", "vk"),
        ("tiktok.com", "tiktok"),
        ("bsky.app", "bluesky"),
        ("bilibili.com", "bilibili"),
    )
    for marker, link_type in known_hosts:
        if host == marker or host.endswith(f".{marker}"):
            return link_type
    return _link_type(label) if label else "external"


def parse_release_window(
    value: dict[str, Any] | None,
) -> tuple[date | None, date | None, str | None, bool | None]:
    """Return the minimum and maximum possible dates from Steam's release text."""

    if not isinstance(value, dict):
        return None, None, None, None
    raw_value = value.get("date")
    raw = raw_value.strip() if isinstance(raw_value, str) else None
    coming_soon = _parse_bool(value.get("coming_soon"))
    if not raw:
        return None, None, raw, coming_soon

    for fmt in ("%b %d, %Y", "%B %d, %Y", "%d %b, %Y", "%Y-%m-%d"):
        try:
            parsed = datetime.strptime(raw, fmt).date()
            return parsed, parsed, raw, coming_soon
        except ValueError:
            continue

    quarter = re.search(r"\bq([1-4])\s*(\d{4})\b", raw, flags=re.IGNORECASE)
    if quarter:
        quarter_number, year = int(quarter.group(1)), int(quarter.group(2))
        month = (quarter_number - 1) * 3 + 1
        minimum = date(year, month, 1)
        last_month = month + 2
        maximum = date(year, last_month, calendar.monthrange(year, last_month)[1])
        return minimum, maximum, raw, coming_soon

    month_match = re.search(
        r"\b(january|february|march|april|may|june|july|august|september|"
        r"october|november|december)\s+(\d{4})\b",
        raw,
        flags=re.IGNORECASE,
    )
    if month_match:
        month_year = int(month_match.group(2))
        month_number = datetime.strptime(month_match.group(1), "%B").month
        return (
            date(month_year, month_number, 1),
            date(month_year, month_number, calendar.monthrange(month_year, month_number)[1]),
            raw,
            coming_soon,
        )

    year_match = re.search(r"\b(\d{4})\b", raw)
    if year_match:
        parsed_year = int(year_match.group(1))
        return date(parsed_year, 1, 1), date(parsed_year, 12, 31), raw, coming_soon
    return None, None, raw, coming_soon


def parse_release_date(value: dict[str, Any] | None) -> tuple[date | None, str | None, bool | None]:
    """Backward-compatible release parser returning the minimum date."""

    minimum, _maximum, raw, coming_soon = parse_release_window(value)
    return minimum, raw, coming_soon


def _release_status(data: dict[str, Any], coming_soon: bool | None) -> str:
    raw_status = data.get("release_state") or data.get("release_status")
    if isinstance(raw_status, str):
        normalized = raw_status.casefold().replace("-", "_").replace(" ", "_")
        if normalized in {"removed", "retired", "unavailable", "delisted"}:
            return "removed"
        if normalized in {"advanced_access", "advancedaccess", "prerelease", "pre_release"}:
            return "advanced_access"
        if normalized in {"early_access", "earlyaccess"}:
            return "early_access"
        if normalized in {"preorder", "pre_order"}:
            return "not_released"
    if _parse_bool(data.get("is_early_access")) or _parse_bool(data.get("early_access")):
        return "early_access"
    if _parse_bool(data.get("is_advanced_access")) or _parse_bool(data.get("advanced_access")):
        return "advanced_access"
    # A preorder is a purchase state, not the TZ release state.
    if _parse_bool(data.get("is_preorder")) or _parse_bool(data.get("preorder")):
        return "not_released"
    return "not_released" if coming_soon is True else "released"


def normalize_release_status(data: dict[str, Any] | None) -> str:
    """Normalize structured Steam release signals to the TZ literals."""

    if not isinstance(data, dict):
        return "released"
    _minimum, _maximum, _raw, coming_soon = parse_release_window(data.get("release_date"))
    return _release_status(data, coming_soon)


def _controller_support_level(value: Any) -> str | None:
    if value is None:
        return None
    normalized = str(value).casefold()
    if "full" in normalized:
        return "full"
    if "partial" in normalized:
        return "partial"
    if "none" in normalized or "no controller" in normalized:
        return "none"
    return normalized.strip() or None


def parse_requirements(data: dict[str, Any] | None) -> Requirements | None:
    if not isinstance(data, dict):
        return None
    minimum = text_value(data.get("minimum"))
    recommended = text_value(data.get("recommended"))
    if minimum is None and recommended is None:
        return None
    return Requirements(minimum=minimum, recommended=recommended)


def parse_system_requirements(data: dict[str, Any] | None) -> list[SystemRequirement]:
    if not isinstance(data, dict):
        return []
    result: list[SystemRequirement] = []
    requirements = (
        ("windows", data.get("pc_requirements")),
        ("mac", data.get("mac_requirements")),
        ("linux", data.get("linux_requirements")),
    )
    for platform, raw in requirements:
        if not isinstance(raw, dict):
            continue
        for level in ("minimum", "recommended"):
            value = raw.get(level)
            if isinstance(value, str) and value.strip():
                result.append(SystemRequirement(platform=platform, level=level, html=value))
    return result


def parse_price(data: Any) -> PriceOverview | None:
    if not isinstance(data, dict):
        return None
    currency = data.get("currency")
    if not isinstance(currency, str) or not currency.strip():
        return None
    return PriceOverview(
        currency=currency,
        initial=_parse_int(data.get("initial")),
        final=_parse_int(data.get("final")),
        discount_percent=_parse_int(data.get("discount_percent")),
        initial_formatted=(
            data["initial_formatted"] if isinstance(data.get("initial_formatted"), str) else None
        ),
        final_formatted=(
            data["final_formatted"] if isinstance(data.get("final_formatted"), str) else None
        ),
    )


def _language(value: str) -> tuple[str | None, str | None]:
    code = steam_name_to_code(value) or canonicalize_language_name(value)
    bcp47 = steam_code_to_bcp47(code)
    return (bcp47, code) if bcp47 else (None, None)


def parse_languages_fallback(value: str | None) -> list[LanguageSupport]:
    if not value:
        return []
    result: list[LanguageSupport] = []
    for raw_part in re.split(r",\s*", value):
        part = plain_text(raw_part).strip().rstrip("*")
        if not part or "languages with" in part.casefold():
            continue
        web_code, steam_language = _language(part)
        result.append(
            LanguageSupport(
                name=part,
                web_code=web_code,
                steam_language=steam_language,
                # appdetails' comma-separated fallback does not tell us
                # which columns are supported.  Do not turn absence of data
                # into fabricated support flags.
                text=None,
                subtitles=None,
                audio="<strong>" in raw_part.lower() or raw_part.rstrip().endswith("*"),
            )
        )
    return result


def parse_appinfo_languages(value: Any) -> list[LanguageSupport]:
    """Parse the structured ``common.supported_languages`` AppInfo map."""

    if not isinstance(value, dict):
        return []
    result: list[LanguageSupport] = []
    for raw_name, raw_flags in value.items():
        if not isinstance(raw_name, str):
            continue
        flags = raw_flags if isinstance(raw_flags, dict) else {}
        web_code, steam_language = _language(raw_name)
        result.append(
            LanguageSupport(
                name=raw_name,
                web_code=web_code,
                steam_language=steam_language,
                text=_parse_bool(flags.get("supported")) is True,
                audio=_parse_bool(flags.get("full_audio")) is True,
                subtitles=_parse_bool(flags.get("subtitles")) is True,
            )
        )
    return result


def parse_language_table(html: str) -> list[LanguageSupport]:
    parser = HTMLParser(html)
    table = parser.css_first("table.game_language_options")
    if table is None:
        return []
    result: list[LanguageSupport] = []
    for row in table.css("tr")[1:]:
        cells = row.css("td")
        if len(cells) < 4:
            continue
        name = _node_text(cells[0])
        if not name:
            continue
        web_code, steam_language = _language(name)
        result.append(
            LanguageSupport(
                name=name,
                web_code=web_code,
                steam_language=steam_language,
                text=bool(cells[1].css_first("span")),
                audio=bool(cells[2].css_first("span")),
                subtitles=bool(cells[3].css_first("span")),
            )
        )
    return result


def parse_tags(html: str) -> list[Tag]:
    parser = HTMLParser(html)
    result: list[Tag] = []
    for rank, node in enumerate(parser.css(".popular_tags .app_tag"), start=1):
        name = _node_text(node)
        if name:
            result.append(Tag(name=name, source="audience", rank=rank))
    return result


def _structured_tag_values(data: dict[str, Any]) -> list[tuple[int, str | None, int | None]]:
    candidates = [data.get("tags"), data.get("store_tags")]
    app_info = data.get("app_info")
    if isinstance(app_info, dict):
        common = app_info.get("common")
        if isinstance(common, dict):
            candidates.extend((common.get("tags"), common.get("store_tags")))
    result: list[tuple[int, str | None, int | None]] = []
    for raw_values in candidates:
        if isinstance(raw_values, dict):
            values = [
                {"tag_id": key, **value}
                if isinstance(value, dict)
                else {"tag_id": key, "name": value}
                for key, value in raw_values.items()
            ]
        else:
            values = _as_list(raw_values)
        for raw in values:
            if not isinstance(raw, dict):
                continue
            tag_id = _parse_int(raw.get("tag_id", raw.get("tagid", raw.get("id"))))
            if tag_id is None:
                continue
            name = raw.get("name", raw.get("tag"))
            weight = _parse_int(raw.get("weight", raw.get("count")))
            result.append(
                (
                    tag_id,
                    str(name).strip() if isinstance(name, str) and name.strip() else None,
                    weight,
                )
            )
    return list(dict.fromkeys(result))


def parse_structured_tags(
    data: dict[str, Any] | None, *, language: str = "en"
) -> tuple[list[Tag], list[TagLocalization]]:
    if not isinstance(data, dict):
        return [], []
    rows = _structured_tag_values(data)
    tags = [Tag(tag_id=tag_id, weight=weight) for tag_id, _name, weight in rows]
    localizations = [
        TagLocalization(tag_id=tag_id, language=language, name=name)
        for tag_id, name, _weight in rows
        if name
    ]
    return tags, localizations


def parse_html_structured_tags(
    html: str | None, *, language: str = "en"
) -> tuple[list[Tag], list[TagLocalization]]:
    if not html:
        return [], []
    names_match = re.search(r'"tags"\s*:\s*(\[[^\]]*\])', html)
    ids_match = re.search(r'"tagids"\s*:\s*(\[[^\]]*\])', html)
    if not names_match or not ids_match:
        return [], []
    try:
        import json

        names = json.loads(names_match.group(1))
        ids = json.loads(ids_match.group(1))
    except (ValueError, TypeError):
        return [], []
    if not isinstance(names, list) or not isinstance(ids, list):
        return [], []
    rows = [
        {"tag_id": tag_id, "name": name}
        for tag_id, name in zip(ids, names, strict=False)
        if _parse_int(tag_id) is not None and isinstance(name, str) and name.strip()
    ]
    return parse_structured_tags({"tags": rows}, language=language)


def parse_html_creator_entities(html: str | None) -> list[dict[str, int]]:
    if not html:
        return []
    match = re.search(r'"creator_clan_ids"\s*:\s*(\[[^\]]*\])', html)
    if not match:
        return []
    try:
        import json

        values = json.loads(match.group(1))
    except (ValueError, TypeError):
        return []
    return [
        {"creator_clan_account_id": int(value)} for value in values if _parse_int(value) is not None
    ]


def parse_structured_genres(
    data: dict[str, Any] | None, *, language: str = "en"
) -> tuple[list[Genre], list[GenreLocalization]]:
    if not isinstance(data, dict):
        return [], []
    result: list[tuple[int, str | None]] = []
    for raw in _as_list(data.get("genres")):
        if isinstance(raw, dict):
            genre_id = _parse_int(raw.get("genre_id", raw.get("id")))
            name = raw.get("name", raw.get("description"))
        else:
            genre_id = None
            name = raw
        if genre_id is None:
            continue
        result.append(
            (genre_id, str(name).strip() if isinstance(name, str) and name.strip() else None)
        )
    return (
        [Genre(genre_id=genre_id) for genre_id, _name in result],
        [
            GenreLocalization(genre_id=genre_id, language=language, name=name)
            for genre_id, name in result
            if name
        ],
    )


def parse_country_restrictions(data: dict[str, Any] | None) -> list[Any]:
    """Normalize package country restrictions without deriving countries from store scope."""

    if not isinstance(data, dict):
        return []
    result: list[Any] = []
    from scraper.models import PackageCountryRestriction

    for package_id, group, item in _package_entries(data):
        raw = item.get("country_restrictions", item.get("country_restriction"))
        if raw is None:
            raw = group.get("country_restrictions", group.get("country_restriction"))
        if isinstance(raw, dict):
            values = [{"country_code": key, "type": value} for key, value in raw.items()]
        else:
            values = _as_list(raw)
        for value in values:
            if isinstance(value, str):
                result.append(
                    PackageCountryRestriction(
                        package_id=package_id,
                        restriction_type="unknown",
                        country_code=value.upper(),
                    )
                )
                continue
            if not isinstance(value, dict):
                continue
            country = value.get("country_code", value.get("country"))
            restriction_type = value.get("restriction_type", value.get("type", "unknown"))
            if isinstance(country, str) and country.strip():
                result.append(
                    PackageCountryRestriction(
                        package_id=package_id,
                        restriction_type=str(restriction_type),
                        country_code=country.strip().upper(),
                    )
                )
    unique: list[PackageCountryRestriction] = []
    seen: set[tuple[int, str, str]] = set()
    for item in result:
        key = (item.package_id, item.restriction_type, item.country_code)
        if key in seen:
            continue
        seen.add(key)
        unique.append(item)
    return unique


def parse_workshop_stats(
    data: dict[str, Any] | None,
    html: str | None = None,
    *,
    collection_html: str | None = None,
    app_id: int = 0,
) -> WorkshopStats:
    common = (
        data.get("common")
        if isinstance(data, dict) and isinstance(data.get("common"), dict)
        else data or {}
    )
    categories = (
        parse_appinfo_category_ids(common.get("category")) if isinstance(common, dict) else []
    )
    category_ids = {item.id for item in categories}
    available = 30 in category_ids if category_ids else None
    published = collections = None
    for source_html, field_name in ((html, "published"), (collection_html, "collections")):
        if not source_html:
            continue
        match = re.search(
            r'\\?"workshopNumbers\\?"\s*:\s*\{[^}]*\\?"total\\?"\s*:\s*(\d+)',
            source_html,
        )
        if match:
            if field_name == "published":
                published = int(match.group(1))
            else:
                collections = int(match.group(1))
    if published is not None:
        available = True
    return WorkshopStats(
        app_id=app_id,
        workshop_available=available,
        published_file_count=published,
        collection_count=collections,
    )


def _achievement_from_data(
    raw: dict[str, Any], *, language: str | None = None
) -> Achievement | None:
    name = raw.get("name") or raw.get("displayName") or raw.get("display_name")
    if not isinstance(name, str) or not name.strip():
        return None
    achievement_id = raw.get("achievement_id", raw.get("apiname"))
    if not isinstance(achievement_id, str) or not achievement_id.strip():
        return None
    return Achievement(
        achievement_id=achievement_id.strip(),
        name=name.strip(),
        description=(str(raw["description"]) if raw.get("description") is not None else None),
        global_percent=_parse_float(raw.get("global_percent", raw.get("percent"))),
        hidden=_parse_bool(raw.get("hidden")),
        icon_url=_optional_url(raw.get("icon") or raw.get("icon_url")),
        language=language,
    )


def parse_achievement_schema(
    data: dict[str, Any] | None,
    *,
    language: str | None = None,
    percentages: dict[str, float] | None = None,
) -> list[Achievement]:
    """Parse the structured ISteamUserStats schema response."""

    if not isinstance(data, dict):
        return []
    game = data.get("game")
    stats = game.get("availableGameStats") if isinstance(game, dict) else None
    raw_achievements = stats.get("achievements") if isinstance(stats, dict) else None
    result: list[Achievement] = []
    for raw in _as_list(raw_achievements):
        if not isinstance(raw, dict):
            continue
        item = dict(raw)
        item["achievement_id"] = raw.get("name")
        item["name"] = raw.get("displayName", raw.get("display_name"))
        item["global_percent"] = (percentages or {}).get(str(raw.get("name")))
        item["icon_url"] = raw.get("icon")
        achievement = _achievement_from_data(item, language=language)
        if achievement is not None:
            result.append(achievement)
    return result


def parse_global_achievement_percentages(data: dict[str, Any] | None) -> dict[str, float]:
    if not isinstance(data, dict):
        return {}
    values = data.get("achievementpercentages")
    raw_achievements = values.get("achievements") if isinstance(values, dict) else None
    result: dict[str, float] = {}
    for raw in _as_list(raw_achievements):
        if not isinstance(raw, dict) or raw.get("name") is None:
            continue
        percent = _parse_float(raw.get("percent"))
        if percent is not None:
            result[str(raw["name"])] = percent
    return result


def parse_app_achievements(
    data: dict[str, Any] | None, *, language: str | None = None
) -> list[Achievement]:
    if not isinstance(data, dict):
        return []
    percentages = data.get("achievements_percent") or {}
    result: list[Achievement] = []
    for raw in _as_list(data.get("achievements")):
        if not isinstance(raw, dict):
            continue
        item = dict(raw)
        api_name = item.get("achievement_id", item.get("apiname"))
        if (
            isinstance(percentages, dict)
            and api_name in percentages
            and isinstance(percentages[api_name], dict)
        ):
            item["percent"] = percentages[api_name].get("percent")
        achievement = _achievement_from_data(item, language=language)
        if achievement is not None:
            result.append(achievement)
    return result


def parse_achievements(html: str, *, language: str | None = None) -> list[Achievement]:
    parser = HTMLParser(html)
    result: list[Achievement] = []
    for row in parser.css(".achieveRow"):
        name = _node_text(row.css_first(".achieveTxt h3"))
        if not name:
            continue
        description = _node_text(row.css_first(".achieveTxt h5")) or None
        percent_text = _node_text(row.css_first(".achievePercent"))
        percent_match = re.search(r"(\d+(?:\.\d+)?)", percent_text)
        icon = row.css_first(".achieveImgHolder img")
        class_name = row.attributes.get("class") or ""
        api_name = row.attributes.get("data-apiname") or row.attributes.get("data-achievement")
        result.append(
            Achievement(
                achievement_id=str(api_name) if api_name is not None else None,
                name=name,
                description=description,
                global_percent=float(percent_match.group(1)) if percent_match else None,
                hidden="hidden" in class_name.casefold(),
                icon_url=_optional_url(icon.attributes.get("src") if icon else None),
                language=language,
            )
        )
    return result


def parse_media(
    data: dict[str, Any], *, language: str | None = None
) -> list[MediaImage | MediaVideo]:
    result: list[MediaImage | MediaVideo] = []
    for item in _as_list(data.get("screenshots")):
        if not isinstance(item, dict):
            continue
        full_url = _optional_url(item.get("path_full") or item.get("full_url") or item.get("url"))
        raw_language = item.get("language")
        result.append(
            MediaImage(
                id=_parse_int(item.get("id")),
                media_type="screenshot",
                type="screenshot",
                thumbnail_url=_optional_url(
                    item.get("path_thumbnail") or item.get("thumbnail_url")
                ),
                full_url=full_url,
                url=full_url,
                format=_format_from_url(full_url),
                language=_language(raw_language)[0] if isinstance(raw_language, str) else None,
            )
        )

    emitted_legacy_types: set[str] = set()
    legacy_keys = set(_LEGACY_ASSET_PRIORITY)
    for key, media_type in _MEDIA_KEY_TYPES.items():
        if key in legacy_keys:
            continue
        url = _optional_url(data.get(key))
        if url:
            result.append(
                MediaImage(
                    media_type=media_type,
                    type=media_type,
                    url=url,
                    full_url=url,
                    format=_format_from_url(url),
                    language=None,
                )
            )
    for key in _LEGACY_ASSET_PRIORITY:
        media_type = _MEDIA_KEY_TYPES[key]
        url = _optional_url(data.get(key))
        if url and media_type not in emitted_legacy_types:
            emitted_legacy_types.add(media_type)
            result.append(
                MediaImage(
                    media_type=media_type,
                    type=media_type,
                    url=url,
                    full_url=url,
                    format=_format_from_url(url),
                    language=None,
                )
            )

    assets = data.get("library_assets_full") or data.get("library_assets")
    if isinstance(assets, dict):
        for key, raw in assets.items():
            canonical_type = _STORE_ASSET_TYPES.get(key) or _canonical_media_type(key)
            if not canonical_type:
                continue
            variants: list[tuple[Any, str | None]] = []
            if isinstance(raw, dict) and any(
                isinstance(value, dict) and "image" in value for value in raw.values()
            ):
                for raw_variant in raw.values():
                    if not isinstance(raw_variant, dict):
                        continue
                    for raw_language, raw_url in (raw_variant.get("image") or {}).items():
                        if isinstance(raw_language, str):
                            variants.append((raw_url, _language(raw_language)[0]))
            elif isinstance(raw, dict) and isinstance(raw.get("image"), dict):
                variants.extend(
                    (raw_url, _language(raw_language)[0])
                    for raw_language, raw_url in raw["image"].items()
                    if isinstance(raw_language, str)
                )
            else:
                variants.append(
                    (raw.get("image") or raw.get("url") or raw.get("filename"), None)
                    if isinstance(raw, dict)
                    else (raw, None)
                )
            for raw_url, raw_language in variants:
                url = _optional_url(raw_url)
                if url:
                    result.append(
                        MediaImage(
                            media_type=canonical_type,
                            type=canonical_type,
                            url=url,
                            full_url=url,
                            format=_format_from_url(url),
                            language=raw_language,
                        )
                    )

    browse_assets = data.get("assets")
    if isinstance(browse_assets, dict):
        asset_format = browse_assets.get("asset_url_format")
        for key, raw in browse_assets.items():
            if key in {"asset_url_format", "small_capsule_2x"}:
                continue
            raw_url = raw if isinstance(raw, str) else None
            if raw_url and isinstance(asset_format, str):
                raw_url = asset_format.replace("${FILENAME}", raw_url)
                raw_url = f"https://cdn.akamai.steamstatic.com/{raw_url}"
            url = _optional_url(raw_url)
            canonical_type = _STORE_ASSET_TYPES.get(key) or _canonical_media_type(key)
            if url and canonical_type:
                result.append(
                    MediaImage(
                        media_type=canonical_type,
                        type=canonical_type,
                        url=url,
                        full_url=url,
                        format=_format_from_url(url),
                        language=None,
                    )
                )

    movies = list(_as_list(data.get("movies")))
    trailers = data.get("trailers")
    if isinstance(trailers, dict):
        for item in _as_list(trailers.get("highlights")):
            if not isinstance(item, dict):
                continue
            webm_filename = next(
                (
                    raw.get("filename")
                    for raw in _as_list(item.get("microtrailer"))
                    if isinstance(raw, dict) and raw.get("type") == "video/webm"
                ),
                None,
            )
            if isinstance(webm_filename, str):
                base = item.get("trailer_url_format")
                webm_url = (
                    base.replace("${FILENAME}", webm_filename)
                    if isinstance(base, str)
                    else f"https://cdn.akamai.steamstatic.com/steam/apps/{webm_filename}"
                )
                if not webm_url.startswith(("http://", "https://")):
                    webm_url = f"https://cdn.akamai.steamstatic.com/{webm_url.lstrip('/')}"
                movies.append(
                    {
                        "id": item.get("trailer_base_id"),
                        "name": item.get("trailer_name"),
                        "webm": {"max": webm_url},
                        "thumbnail": item.get("screenshot_full"),
                    }
                )

    for item in movies:
        if not isinstance(item, dict):
            continue
        webm_value = item.get("webm")
        webm_data: dict[str, Any] = webm_value if isinstance(webm_value, dict) else {}
        webm_max = _optional_url(webm_data.get("max"))
        webm_480 = _optional_url(webm_data.get("480"))
        hls_url = _optional_url(item.get("hls_h264"))
        dash_h264_url = _optional_url(item.get("dash_h264"))
        dash_av1_url = _optional_url(item.get("dash_av1"))
        # Steam's WebM files are stable downloadable media. Prefer them over
        # DASH manifests, which are not useful as a canonical media URL.
        primary_url = next(
            (url for url in (webm_max, webm_480, hls_url, dash_h264_url, dash_av1_url) if url),
            None,
        )
        result.append(
            MediaVideo(
                id=_parse_int(item.get("id")),
                name=item.get("name") if isinstance(item.get("name"), str) else None,
                thumbnail_url=_optional_url(item.get("thumbnail")),
                dash_av1_url=dash_av1_url,
                dash_h264_url=dash_h264_url,
                hls_h264_url=hls_url,
                highlight=_parse_bool(item.get("highlight")),
                media_type="trailer",
                type="trailer",
                format=_format_from_url(primary_url),
                language=None,
                url=primary_url,
            )
        )
    return result


def _package_entries(data: dict[str, Any]) -> list[tuple[int, dict[str, Any], dict[str, Any]]]:
    result: list[tuple[int, dict[str, Any], dict[str, Any]]] = []
    for group in _as_list(data.get("package_groups")):
        if not isinstance(group, dict):
            continue
        for raw in _as_list(group.get("subs")) + _as_list(group.get("packages")):
            if not isinstance(raw, dict):
                continue
            package_id = _parse_int(raw.get("packageid", raw.get("package_id", raw.get("id"))))
            if package_id is not None:
                result.append((package_id, group, raw))
    for raw in _as_list(data.get("packages")):
        package_id = _parse_int(raw.get("packageid") if isinstance(raw, dict) else raw)
        if package_id is not None and not any(item[0] == package_id for item in result):
            result.append((package_id, {}, raw if isinstance(raw, dict) else {}))
    return result


def _edition_name(group: dict[str, Any], item: dict[str, Any]) -> str | None:
    for value in (
        item.get("name"),
        item.get("option_text"),
        item.get("purchase_option_name"),
        group.get("name"),
    ):
        if isinstance(value, str) and value.strip():
            name = plain_text(value).strip()
            name = re.sub(r"^(?:buy|purchase)\s+", "", name, flags=re.IGNORECASE).strip()
            # Store HTML sometimes appends the display price to the option
            # label.  Price belongs to the price row, never to package name.
            return (
                re.sub(
                    r"\s+(?:[$€£]\s*[\d,.]+|[\d,.]+\s*(?:USD|EUR|GBP|RUB))$",
                    "",
                    name,
                    flags=re.IGNORECASE,
                )
                .rstrip(" -–—:")
                .strip()
            )
    return None


def parse_editions(
    data: dict[str, Any], *, currency: str | None = None
) -> tuple[list[int], list[EditionInfo], list[EditionPrice]]:
    package_ids: list[int] = []
    editions: list[EditionInfo] = []
    prices: list[EditionPrice] = []
    for package_id, group, item in _package_entries(data):
        package_ids.append(package_id)
        editions.append(
            EditionInfo(
                package_id=package_id,
                name=_edition_name(group, item),
                description=(
                    str(item["description"])
                    if item.get("description") is not None
                    else (
                        str(group["description"]) if group.get("description") is not None else None
                    )
                ),
            )
        )
        initial = _parse_int(
            item.get("price_in_cents", item.get("initial", group.get("price_in_cents")))
        )
        final = _parse_int(
            item.get(
                "price_in_cents_with_discount",
                item.get("final", group.get("price_in_cents_with_discount", initial)),
            )
        )
        recurring = item.get("recurring_sub") or group.get("recurring_sub") or {}
        if not isinstance(recurring, dict):
            recurring = {}
        prices.append(
            EditionPrice(
                package_id=package_id,
                price_region=_price_region(item) or _price_region(group),
                initial=initial,
                final=final,
                discount_percent=_parse_int(
                    item.get("percent_savings", group.get("percent_savings"))
                ),
                price_type="recurring"
                if _parse_bool(
                    item.get("is_recurring_subscription", group.get("is_recurring_subscription"))
                )
                else "one_time",
                period=(str(recurring["period"]) if recurring.get("period") is not None else None),
                period_units=_parse_int(recurring.get("frequency", recurring.get("period_units"))),
                currency=(
                    str(item.get("currency") or group.get("currency") or currency).upper()
                    if item.get("currency") or group.get("currency") or currency
                    else None
                ),
                discount_description=_discount_metadata(item)[0],
                discount_end_at=_discount_metadata(item)[1],
            )
        )
    return package_ids, editions, prices


def parse_package_metadata(data: dict[int, dict[str, Any]] | None) -> list[EditionInfo]:
    """Normalize public packagedetails rows without creating app links."""

    if not isinstance(data, dict):
        return []
    result: list[EditionInfo] = []
    for raw_package_id, raw in data.items():
        package_id = _parse_int(raw_package_id)
        if package_id is None or not isinstance(raw, dict):
            continue
        description = raw.get("description")
        result.append(
            EditionInfo(
                package_id=package_id,
                name=_edition_name({}, raw),
                description=plain_text(description).strip()
                if isinstance(description, str) and plain_text(description).strip()
                else None,
            )
        )
    return result


def parse_bundles(
    data: dict[str, Any], *, currency: str | None = None
) -> tuple[list[Bundle], list[BundlePrice]]:
    bundles: list[Bundle] = []
    prices: list[BundlePrice] = []
    for raw in _as_list(data.get("bundles")):
        if not isinstance(raw, dict):
            continue
        bundle_id = _parse_int(raw.get("bundleid", raw.get("bundle_id", raw.get("id"))))
        if bundle_id is None:
            continue
        bundles.append(
            Bundle(
                bundle_id=bundle_id,
                name=raw.get("name") if isinstance(raw.get("name"), str) else None,
                discount_percent=_parse_int(raw.get("discount_pct", raw.get("discount_percent"))),
                must_purchase_as_set=_parse_bool(raw.get("must_purchase_as_set")),
                # Membership is intentionally absent here.  AppDetails and
                # app-level StoreBrowse data do not identify the contents of
                # each bundle reliably; only a bundle-specific response may
                # populate this relation.
                edition_package_ids=[],
            )
        )
        initial = _parse_int(raw.get("price_before_discount", raw.get("initial")))
        final = _parse_int(raw.get("price", raw.get("final")))
        prices.append(
            BundlePrice(
                bundle_id=bundle_id,
                price_region=_price_region(raw),
                effective_discount_percent=_parse_int(
                    raw.get("discount_pct", raw.get("discount_percent"))
                ),
                initial=initial,
                final=final,
                currency=(
                    str(raw.get("currency") or currency).upper()
                    if raw.get("currency") or currency
                    else None
                ),
                discount_description=_discount_metadata(raw)[0],
                discount_end_at=_discount_metadata(raw)[1],
            )
        )
    return bundles, prices


def _link(link_type: str, value: Any) -> ExternalLink | None:
    if isinstance(value, dict):
        url = _optional_url(value.get("url"))
        raw_value = value.get("value")
    else:
        url = _optional_url(value)
        raw_value = None
    if url or raw_value not in (None, ""):
        return ExternalLink(
            type=link_type, url=url, value=str(raw_value) if raw_value not in (None, "") else None
        )
    return None


def _normalize_external_link(link: ExternalLink) -> ExternalLink | None:
    url = link.url
    value = link.value.strip() if isinstance(link.value, str) and link.value.strip() else None
    if url is None and value is not None:
        if value.casefold().startswith("mailto:"):
            value = value[7:].strip()
        elif value.startswith(("http://", "https://", "//")):
            url = _optional_url(value)
            value = None if url else value
    if url is not None:
        link_type = _social_link_type(url)
        if link_type == "external" and link.type in {
            "support_website",
            "support_email",
            "website",
        }:
            link_type = "support_website" if link.type != "website" else "website"
        return ExternalLink(type=link_type, url=url, value=None)
    if value is None:
        return None
    return ExternalLink(
        type=(
            "support_email"
            if "@" in value and link.type in {"support_email", "email"}
            else link.type
        ),
        value=value,
    )


def parse_external_links(data: dict[str, Any], html: str | None = None) -> list[ExternalLink]:
    result: list[ExternalLink] = []
    website = _link("website", data.get("website"))
    if website:
        result.append(website)
    support = data.get("support_info")
    if isinstance(support, dict):
        support_website = _link("support_website", support.get("url", support.get("support_url")))
        if support_website:
            result.append(support_website)
        email = support.get("email")
        if email:
            result.append(ExternalLink(type="support_email", value=str(email)))
    for raw in _as_list(data.get("external_links")):
        if not isinstance(raw, dict):
            continue
        link = _link(_link_type(str(raw.get("type", "external"))), raw)
        if link:
            result.append(link)
    structured_link_types = {
        1: "youtube",
        2: "facebook",
        3: "twitter",
        4: "twitch",
        5: "discord",
    }
    for raw in _as_list(data.get("links")):
        if not isinstance(raw, dict):
            continue
        raw_link_type = _parse_int(raw.get("link_type"))
        link_type = structured_link_types.get(raw_link_type) if raw_link_type is not None else None
        if link_type is None:
            link_type = _link_type(str(raw.get("type", "external")))
        link = _link(link_type, raw)
        if link:
            result.append(link)
    social_values = data.get("social_media")
    if isinstance(social_values, dict):
        social_values = [{"type": key, "url": value} for key, value in social_values.items()]
    for raw in _as_list(social_values):
        if not isinstance(raw, dict):
            continue
        link = _link(_link_type(str(raw.get("type", "external"))), raw)
        if link:
            result.append(link)
    if html:
        parser = HTMLParser(html)
        for node in parser.css("a"):
            raw_href = node.attributes.get("href")
            if isinstance(raw_href, str) and raw_href.casefold().startswith("mailto:"):
                result.append(ExternalLink(type="support_email", value=raw_href[7:]))
                continue
            href = _optional_url(raw_href)
            classes = (node.attributes.get("class") or "").casefold()
            label = _node_text(node).casefold()
            if not href or not any(
                token in f"{classes} {label}"
                for token in ("external", "social", "support", "official", "website")
            ):
                continue
            result.append(ExternalLink(type=_social_link_type(href, label), url=href))
    normalized_links = [
        normalized for link in result if (normalized := _normalize_external_link(link)) is not None
    ]
    unique: list[ExternalLink] = []
    seen: set[tuple[str, str | None, str | None]] = set()
    for link in normalized_links:
        key = (link.type, link.url, link.value)
        if key not in seen:
            seen.add(key)
            unique.append(link)
    return unique


def parse_age_ratings(data: dict[str, Any] | None) -> list[AgeRating]:
    if not isinstance(data, dict):
        return []
    result: list[AgeRating] = []
    for authority, raw in data.items():
        if not isinstance(raw, dict):
            continue
        raw_descriptors = str(raw.get("descriptors", ""))
        descriptors = normalize_descriptor_text(raw_descriptors)
        authority_name = str(authority).upper()
        rating_value = str(raw["rating"]) if raw.get("rating") is not None else None
        minimum_age = _parse_int(raw.get("required_age"))
        if minimum_age is None and rating_value:
            normalized_rating = rating_value.strip().upper().replace("+", "")
            rating_map = {
                "EC": 3,
                "E": 6,
                "E10": 10,
                "T": 13,
                "M": 17,
                "AO": 18,
                "USK 0": 0,
                "USK 6": 6,
                "USK 12": 12,
                "USK 16": 16,
                "USK 18": 18,
            }
            if authority_name == "PEGI":
                minimum_age = (
                    _parse_int(re.search(r"\d+", normalized_rating).group())
                    if re.search(r"\d+", normalized_rating)
                    else None
                )
            else:
                minimum_age = rating_map.get(normalized_rating)
        result.append(
            AgeRating(
                authority=str(authority),
                age_id=str(authority),
                rating=rating_value,
                minimum_age=minimum_age,
                descriptors=descriptors,
                banned=_parse_bool(raw.get("banned")),
                use_age_gate=_parse_bool(raw.get("use_age_gate")),
                rating_generated=_parse_bool(raw.get("rating_generated")),
                raw=raw_descriptors or None,
            )
        )
    return result


def normalize_descriptor_text(
    value: str | None, *, title: str | None = None, language: str | None = None
) -> list[str]:
    """Create deterministic descriptor fragments while retaining raw source separately."""

    if not isinstance(value, str):
        return []
    text = plain_text(unicodedata.normalize("NFKC", value)).strip()
    if not text:
        return []
    if title:
        subject = re.escape(plain_text(title).strip())
        text = re.sub(
            rf"^\s*{subject}\s+(?:contains|contain|includes|has)\s+",
            "",
            text,
            flags=re.IGNORECASE,
        )
    fragments = re.split(r"\s*(?:\r?\n|;|,|\.|/|\\|\||\band\b|\bor\b)\s*", text, flags=re.I)
    result: list[str] = []
    for fragment in fragments:
        normalized = re.sub(r"\s+", " ", fragment).strip()
        if (
            not normalized
            or _DESCRIPTOR_METADATA.search(normalized)
            or re.fullmatch(r"[\W\d_]+", normalized)
        ):
            continue
        tokens = normalized.casefold().split()
        stopwords = set(_DESCRIPTOR_STOPWORDS)
        if _stopwordsiso is not None:
            for stopword_language in (language, "en", "ru", "de", "fr", "es", "it", "pl"):
                if stopword_language:
                    try:
                        stopwords.update(_stopwordsiso.stopwords(stopword_language.split("-")[0]))
                    except (KeyError, TypeError):
                        pass
        meaningful = [
            token for token in tokens if token not in stopwords or token in _DESCRIPTOR_NEGATIONS
        ]
        if not meaningful:
            continue
        if _simplemma_lemma is not None:
            lemma_language = (language or "en").split("-")[0]
            meaningful = [_simplemma_lemma(token, lemma_language) or token for token in meaningful]
        cleaned = " ".join(meaningful)
        if cleaned not in result:
            result.append(cleaned)
    return result


def parse_descriptors(
    data: dict[str, Any] | None, ratings: dict[str, Any] | None = None
) -> list[Descriptor]:
    result: list[Descriptor] = []
    if isinstance(data, dict):
        for raw_id in _as_list(data.get("ids")):
            descriptor_id = _parse_int(raw_id)
            if descriptor_id is not None:
                result.append(
                    Descriptor(
                        age_id="steam",
                        steam_id=descriptor_id,
                        name=_CONTENT_DESCRIPTOR_NAMES.get(descriptor_id),
                    )
                )
        notes = data.get("notes")
        if isinstance(notes, str) and notes.strip():
            result.extend(
                Descriptor(age_id="steam", name=name) for name in normalize_descriptor_text(notes)
            )
        if _parse_bool(data.get("display_online_notice")):
            result.append(
                Descriptor(
                    age_id="steam",
                    name="Online Interactions Not Rated by the ESRB",
                )
            )
    if isinstance(ratings, dict):
        for authority, raw in ratings.items():
            if not isinstance(raw, dict):
                continue
            for descriptor in normalize_descriptor_text(str(raw.get("descriptors", ""))):
                result.append(Descriptor(age_id=str(authority), name=descriptor))
    unique: list[Descriptor] = []
    seen: set[tuple[str, int | None, str | None]] = set()
    for item in result:
        key = (item.age_id, item.steam_id, item.name)
        if key not in seen:
            seen.add(key)
            unique.append(item)
    return unique


def parse_features(data: dict[str, Any] | None) -> list[Category]:
    if not isinstance(data, dict):
        return []
    result: list[Category] = []
    for raw in _as_list(data.get("categories")):
        if isinstance(raw, Category):
            result.append(raw)
            continue
        if not isinstance(raw, dict) or not raw.get("description"):
            continue
        result.append(Category(id=_parse_int(raw.get("id")), name=str(raw["description"])))
    return result


def parse_accessibility_features(
    data: dict[str, Any] | None, html: str | None = None
) -> list[Feature]:
    result: list[Feature] = []
    if isinstance(data, dict):
        raw_values = data.get(
            "accessibility_features", data.get("accessibility_options", data.get("accessibility"))
        )
        for raw in _as_list(raw_values):
            if isinstance(raw, dict):
                name = raw.get("name", raw.get("description"))
                feature_id = _parse_int(raw.get("id"))
            else:
                name, feature_id = raw, None
            if isinstance(name, str) and name.strip():
                result.append(Feature(id=feature_id, name=plain_text(name)))
    if html:
        parser = HTMLParser(html)
        for node in parser.css(
            "[data-accessibility], .game_area_accessibility li, .accessibility_feature"
        ):
            name = _node_text(node)
            if name:
                result.append(
                    Feature(id=_parse_int(node.attributes.get("data-accessibility-id")), name=name)
                )
    unique: list[Feature] = []
    seen: set[tuple[int | None, str | None]] = set()
    for feature in result:
        key = (feature.id, feature.name)
        if key not in seen:
            seen.add(key)
            unique.append(feature)
    return unique


def _deck_status(raw: Any) -> str:
    if isinstance(raw, SteamDeckSupport):
        raw = raw.status
    if isinstance(raw, dict):
        raw = raw.get("category", raw.get("status", raw.get("steam_deck_status")))
    if isinstance(raw, (int, float)):
        return {0: "unknown", 1: "unsupported", 2: "playable", 3: "supported"}.get(
            int(raw), "unknown"
        )
    normalized = str(raw or "unknown").casefold().replace("-", "_").replace(" ", "_")
    if normalized in {"verified", "supported", "fully_supported"}:
        return "supported"
    if normalized in {"playable", "works"}:
        return "playable"
    if normalized in {"unsupported", "not_supported"}:
        return "unsupported"
    return "unknown"


def parse_steam_deck(data: dict[str, Any] | None, html: str | None = None) -> SteamDeckSupport:
    raw: Any = None
    if isinstance(data, dict):
        raw = data.get("steam_deck", data.get("deck_compatibility", data.get("steamdeck")))
    if raw is None and html:
        match = re.search(
            r"(?:deck_compatibility|steam_deck)[^:{]*:\s*\{[^}]*?\"?category\"?\s*:\s*(\d+)", html
        )
        if match:
            raw = int(match.group(1))
    return SteamDeckSupport(status=_deck_status(raw))


def parse_eulas(data: dict[str, Any] | None, html: str | None = None) -> list[ThirdPartyEula]:
    result: list[ThirdPartyEula] = []
    if isinstance(data, dict):
        raw_eulas = data.get("eulas", data.get("eula"))
        if isinstance(raw_eulas, dict):
            eula_values = [
                {"id": key, **value} if isinstance(value, dict) else {"id": key}
                for key, value in raw_eulas.items()
            ]
        else:
            eula_values = _as_list(raw_eulas)
        for raw in eula_values:
            if not isinstance(raw, dict):
                continue
            result.append(
                ThirdPartyEula(
                    id=(
                        _parse_int(raw.get("id", raw.get("eulaid")))
                        if _parse_int(raw.get("id", raw.get("eulaid"))) is not None
                        else str(raw.get("id", raw.get("eulaid")))
                        if raw.get("id", raw.get("eulaid")) is not None
                        else None
                    ),
                    name_description=(
                        str(raw["name_description"])
                        if raw.get("name_description") is not None
                        else str(raw["name"])
                        if raw.get("name") is not None
                        else None
                    ),
                    url=_optional_url(raw.get("url")),
                    version=raw.get("version"),
                )
            )
    if html:
        parser = HTMLParser(html)
        for node in parser.css("a.eula, a[data-eula-id], .eula a"):
            url = _optional_url(node.attributes.get("href"))
            if url:
                result.append(
                    ThirdPartyEula(
                        id=node.attributes.get("data-eula-id"),
                        name_description=_node_text(node),
                        url=url,
                    )
                )
    return result


def parse_controllers(data: dict[str, Any] | None, html: str | None = None) -> list[Controller]:
    result: list[Controller] = []
    if isinstance(data, dict):
        raw_values: Any = data.get("controllers", data.get("supported_controllers"))
        if isinstance(raw_values, dict):
            raw_values = [
                {"name": key, **(value if isinstance(value, dict) else {"supported": value})}
                for key, value in raw_values.items()
            ]
        for raw in _as_list(raw_values):
            if isinstance(raw, dict) and isinstance(raw.get("name"), str):
                result.append(
                    Controller(
                        name=raw["name"],
                        bluetooth=_parse_bool(raw.get("bluetooth", raw.get("bt"))),
                        usb=_parse_bool(raw.get("usb")),
                    )
                )
            elif isinstance(raw, str) and raw.strip():
                result.append(Controller(name=raw.strip()))
    if html:
        parser = HTMLParser(html)
        for node in parser.css("[data-controller]"):
            name = node.attributes.get("data-controller") or _node_text(node)
            if name:
                result.append(Controller(name=name))
    unique: list[Controller] = []
    seen: set[str] = set()
    for controller in result:
        if controller.name not in seen:
            seen.add(controller.name)
            unique.append(controller)
    return unique


def parse_appinfo_category_ids(
    value: Any, *, registry: dict[int, str] | None = None
) -> list[Category]:
    """Map AppInfo category ids to the TZ category/accessibility split."""

    if isinstance(value, dict):
        nested = value.get("category", value.get("categories"))
        if nested is not None:
            value = nested
        else:
            value = [
                int(key.removeprefix("category_"))
                for key in value
                if isinstance(key, str) and key.startswith("category_") and key[9:].isdigit()
            ]
    result: list[Category] = []
    for raw in _as_list(value):
        category_id = _parse_int(raw.get("id") if isinstance(raw, dict) else raw)
        if category_id is None:
            continue
        if isinstance(raw, dict):
            raw_name = raw.get("description") or raw.get("name")
            name = str(raw_name) if raw_name else None
        else:
            name = None
        if name is None:
            name = (registry or {}).get(category_id) or _STEAM_CATEGORY_NAMES.get(category_id)
        result.append(Category(id=category_id, name=name))
    return result


def parse_appinfo_controller_configs(config: dict[str, Any] | None) -> list[Controller]:
    """Normalize config.steamcontrollerconfigdetails to canonical controller rows."""

    if not isinstance(config, dict):
        return []
    details = config.get("steamcontrollerconfigdetails")
    if not isinstance(details, dict):
        return []
    names = {
        "controller_ps4": "DualShock 4",
        "controller_ps5": "DualSense",
        "controller_xbox360": "Xbox 360",
        "controller_xboxone": "Xbox One",
        "controller_xboxelite": "Xbox Elite",
        "controller_switch_pro": "Switch Pro",
        "controller_switch2_pro": "Switch 2 Pro",
        "controller_switch_joycon_left": "Switch Joy-Con Left",
        "controller_switch_joycon_right": "Switch Joy-Con Right",
        "controller_switch_joycon_pair": "Switch Joy-Con Pair",
        "controller_steamcontroller_gordon": "Steam Controller",
        "controller_neptune": "Steam Deck Controller",
        "controller_generic": "Generic Controller",
    }
    result: dict[str, Controller] = {}
    for raw in details.values():
        if not isinstance(raw, dict):
            continue
        controller_type = raw.get("controller_type")
        if not isinstance(controller_type, str):
            continue
        name = names.get(
            controller_type,
            controller_type.removeprefix("controller_").replace("_", " ").title(),
        )
        current = result.get(name)
        if current is None:
            result[name] = Controller(name=name)
    return list(result.values())


def parse_appinfo_semantics(
    common: dict[str, Any],
    *,
    registry: dict[int, str] | None = None,
    config: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Extract structured category/controller/deck semantics from AppInfo."""

    categories = parse_appinfo_category_ids(common.get("category"), registry=registry)
    category_ids = [item.id for item in categories if item.id is not None]
    controller_support = _controller_support_level(common.get("controller_support")) or "none"
    deck = common.get("steam_deck_compatibility")
    if not isinstance(deck, dict):
        deck = {"category": deck}
    category = _parse_int(deck.get("category"))
    # Category 60 means Steam Input/gamepad preferred; category 8 is VAC.
    deck_status = (
        {0: "unknown", 1: "unsupported", 2: "playable", 3: "supported"}.get(category, "unknown")
        if category is not None
        else "unknown"
    )
    accessibility_features = [
        Category(
            id=item,
            name=(registry or {}).get(item) or _STEAM_CATEGORY_NAMES.get(item),
        )
        for item in category_ids
        if 64 <= item <= 79
    ]
    unknown_category_ids = sorted(
        {
            item.id
            for item in categories + accessibility_features
            if item.id is not None and item.name is None
        }
    )
    controller_rows: list[Controller] = []
    seen_controller_names: set[str] = set()
    for controller in parse_appinfo_controllers(category_ids) + parse_appinfo_controller_configs(
        config
    ):
        if controller.name in seen_controller_names:
            continue
        seen_controller_names.add(controller.name)
        controller_rows.append(controller)
    return {
        "categories": categories,
        "accessibility_features": accessibility_features,
        "vac_enabled": True if 8 in category_ids else None,
        "gamepad_preferred": 60 in category_ids,
        "controller_support": controller_support,
        "controllers": controller_rows,
        "unknown_category_ids": unknown_category_ids,
        "deck_support": SteamDeckSupport(status=deck_status),
    }


def parse_appinfo_controllers(category_ids: list[int]) -> list[Controller]:
    merged: dict[str, Controller] = {}
    mappings = {
        55: ("DualShock 4", False, True),
        56: ("DualShock 4", True, False),
        57: ("DualSense", False, True),
        58: ("DualSense", True, False),
    }
    for category_id in category_ids:
        mapped = mappings.get(category_id)
        if mapped:
            name, bluetooth, usb = mapped
            current = merged.get(name)
            if current is None:
                merged[name] = Controller(name=name, bluetooth=bluetooth, usb=usb)
            else:
                current.bluetooth = current.bluetooth or bluetooth
                current.usb = current.usb or usb
    return list(merged.values())


def parse_organizations(data: dict[str, Any] | None) -> list[OrganizationCredit]:
    if not isinstance(data, dict):
        return []
    result: list[OrganizationCredit] = []
    for key, status in (("developers", "developer"), ("publishers", "publisher")):
        for raw in _as_list(data.get(key)):
            if isinstance(raw, dict):
                name = raw.get("name")
            else:
                name = raw
            if isinstance(name, str) and name.strip():
                creator_id = (
                    _parse_int(raw.get("creator_clan_account_id", raw.get("clan_account_id")))
                    if isinstance(raw, dict)
                    else None
                )
                result.append(
                    OrganizationCredit(
                        credited_name=name.strip(),
                        status=status,
                        creator_clan_account_id=creator_id,
                    )
                )
    return result


def parse_depots(data: dict[str, Any] | None) -> dict[str, list[Any]]:
    """Normalize AppInfo depots into depot, OS, and branch-manifest rows."""

    if not isinstance(data, dict) or not isinstance(data.get("depots"), dict):
        return {"depots": [], "depot_os": [], "manifests": [], "app_depots": []}
    from scraper.models import Depot, DepotManifest

    raw_depots = data["depots"]
    depots: list[Depot] = []
    depot_os: list[tuple[int, str]] = []
    manifests: list[DepotManifest] = []
    app_depots: list[int] = []
    for raw_id, raw in raw_depots.items():
        depot_id = _parse_int(raw_id)
        if depot_id is None or not isinstance(raw, dict):
            continue
        config = raw.get("config") if isinstance(raw.get("config"), dict) else {}
        dlc_app_id = _parse_int(raw.get("dlcappid", raw.get("dlc_app_id")))
        optional_dlc_app_id = _parse_int(raw.get("optional_dlc_app_id"))
        depot_from_app = _parse_int(raw.get("depotfromapp", raw.get("depot_from_app")))
        depots.append(
            Depot(
                depot_id=depot_id,
                name=str(raw["name"]) if raw.get("name") is not None else None,
                language=(
                    str(config["language"]).strip()
                    if config.get("language") is not None and str(config["language"]).strip()
                    else None
                ),
                architecture=str(config.get("osarch"))
                if config.get("osarch") is not None
                else None,
                low_violence=_parse_bool(config.get("lowviolence", raw.get("lowviolence"))),
                dlc_app_id=dlc_app_id,
                optional_dlc_app_id=optional_dlc_app_id,
                depot_from_app=depot_from_app,
                shared_install=_parse_bool(config.get("sharedinstall", raw.get("sharedinstall"))),
                system_defined=_parse_bool(config.get("system_defined", raw.get("system_defined"))),
            )
        )
        app_depots.append(depot_id)
        raw_os = config.get("oslist")
        os_values = raw_os.split(",") if isinstance(raw_os, str) else _as_list(raw_os)
        for value in os_values:
            if isinstance(value, str) and value.strip():
                depot_os.append((depot_id, value.strip().casefold().replace("macos", "mac")))
        raw_manifests = raw.get("manifests")
        if isinstance(raw_manifests, dict):
            for branch, manifest in raw_manifests.items():
                if not isinstance(branch, str) or not isinstance(manifest, dict):
                    continue
                manifests.append(
                    DepotManifest(
                        depot_id=depot_id,
                        branch=branch,
                        manifest_id=str(manifest.get("gid", manifest.get("manifestid")))
                        if manifest.get("gid", manifest.get("manifestid")) is not None
                        else None,
                        download_size=_install_size(manifest.get("download")),
                        disk_size=_install_size(manifest.get("size", manifest.get("disk"))),
                    )
                )
    return {
        "depots": depots,
        "depot_os": depot_os,
        "manifests": manifests,
        "app_depots": app_depots,
    }


def _install_size(value: Any) -> int | None:
    """Normalize Steam's zero download/disk sentinel to unknown."""

    parsed = _parse_int(value)
    return parsed if parsed is not None and parsed > 0 else None


def _branch_size_profiles(data: dict[str, Any], branch_name: str) -> tuple[list[int], list[int]]:
    rows = parse_depots(data)
    raw_depots = data.get("depots") if isinstance(data.get("depots"), dict) else {}
    grouped: dict[tuple[str, str, str], list[tuple[int | None, int | None]]] = {}
    for manifest in rows["manifests"]:
        if manifest.branch != branch_name:
            continue
        raw = raw_depots.get(str(manifest.depot_id), {}) if isinstance(raw_depots, dict) else {}
        if not isinstance(raw, dict) or raw.get("dlcappid") is not None:
            continue
        config = raw.get("config") if isinstance(raw.get("config"), dict) else {}
        raw_os = config.get("oslist")
        os_name = (
            str(raw_os).split(",")[0].strip().casefold().replace("macos", "mac")
            if isinstance(raw_os, str) and raw_os.strip()
            else "unknown"
        )
        architecture = str(config.get("osarch") or "unknown").casefold()
        language = str(config.get("language") or "unknown").casefold()
        grouped.setdefault((os_name, architecture, language), []).append(
            (manifest.download_size, manifest.disk_size)
        )
    downloads: list[int] = []
    disks: list[int] = []
    for values in grouped.values():
        known_disk = [value for _download, value in values if value is not None]
        if known_disk:
            disks.append(sum(known_disk))
        if len(values) == len([value for value, _disk in values if value is not None]):
            downloads.append(sum(value for value, _disk in values if value is not None))
    return downloads, disks


def _size_stats(values: list[int]) -> tuple[int | None, int | None, int | None]:
    if not values:
        return None, None, None
    return min(values), int(statistics.median(values)), max(values)


def parse_build_branches(data: dict[str, Any] | None) -> list[BuildBranch]:
    if not isinstance(data, dict):
        return []
    raw_branches: Any = data.get("branches", data.get("build_branches", data.get("builds")))
    if raw_branches is None and isinstance(data.get("depots"), dict):
        # Public Steam AppInfo keeps app branches under depots.branches.
        raw_branches = data["depots"].get("branches")
    if isinstance(raw_branches, dict):
        raw_branches = [
            {"name": name, **(value if isinstance(value, dict) else {})}
            for name, value in raw_branches.items()
        ]
    result: list[BuildBranch] = []
    for raw in _as_list(raw_branches):
        if not isinstance(raw, dict):
            continue
        if any(_parse_bool(raw.get(key)) for key in ("private", "password", "pwdrequired")):
            continue
        branch_name = raw.get("name", raw.get("branch", "public"))
        if not isinstance(branch_name, str) or not branch_name.strip():
            continue
        updated = _parse_int(
            raw.get("timeupdated", raw.get("timebuildupdated", raw.get("updated_at")))
        )
        download_sizes, disk_sizes = _branch_size_profiles(data, branch_name)
        if not download_sizes and raw.get("download_size") is not None:
            direct_download = _install_size(raw.get("download_size"))
            direct_disk = _install_size(raw.get("disk_size"))
            if direct_download is not None:
                download_sizes = [direct_download]
            if direct_disk is not None:
                disk_sizes = [direct_disk]
        download_min, download_median, download_max = _size_stats(download_sizes)
        disk_min, disk_median, disk_max = _size_stats(disk_sizes)
        result.append(
            BuildBranch(
                name=branch_name,
                updated_at=(datetime.fromtimestamp(updated, tz=UTC) if updated else None),
                description=raw.get("description"),
                build_id=_parse_int(raw.get("buildid", raw.get("build_id"))),
                download_size_min=download_min,
                download_size_median=download_median,
                download_size_max=download_max,
                disk_size_min=disk_min,
                disk_size_median=disk_median,
                disk_size_max=disk_max,
            )
        )
    # AppInfo contains the same branch once per depot. Keep the newest
    # observation for each public branch name.
    unique: dict[str, BuildBranch] = {}
    for branch in result:
        current = unique.get(branch.name)
        current_time = current.updated_at.timestamp() if current and current.updated_at else -1
        branch_time = branch.updated_at.timestamp() if branch.updated_at else -1
        if current is None or branch_time >= current_time:
            unique[branch.name] = branch
    return list(unique.values())


def parse_review_language_stats(data: Any) -> list[ReviewLanguageStats]:
    if isinstance(data, dict):
        values = [
            {"language": key, **(value if isinstance(value, dict) else {})}
            for key, value in data.items()
        ]
    else:
        values = _as_list(data)
    result: list[ReviewLanguageStats] = []
    for raw in values:
        if not isinstance(raw, dict):
            continue
        language = raw.get("language")
        if isinstance(language, str):
            if language.strip().casefold() in {"", "all", "*"}:
                language = "*"
            else:
                web_code, _steam_language = _language(language)
                language = web_code or language
        else:
            language = "*"
        positive = _parse_int(raw.get("total_positive")) or 0
        negative = _parse_int(raw.get("total_negative")) or 0
        result.append(
            ReviewLanguageStats(
                language=language,
                total_reviews=_parse_int(raw.get("total_reviews")) or positive + negative,
                total_positive=positive,
                total_negative=negative,
                review_score=_parse_int(raw.get("review_score")),
            )
        )
    return result


def parse_external_reviews_html(html: str | None) -> list[ExternalReview]:
    if not html:
        return []
    parser = HTMLParser(html)
    node = parser.css_first("#game_area_reviews")
    if node is None:
        return []
    raw = node.html or ""
    result: list[ExternalReview] = []
    anchor_re = re.compile(
        r"(?P<quote>[^<]{10,}?)\s*<br\s*/?>\s*"
        r"(?P<rating>(?:\d+(?:\.\d+)?\s*/\s*\d+)?)?[^<]*"
        r"<a\s+href=[\"'](?P<url>[^\"']+)[\"'][^>]*>\s*"
        r"(?P<organization>[^<]+?)\s*</a>",
        flags=re.IGNORECASE | re.DOTALL,
    )
    for match in anchor_re.finditer(raw):
        url = match.group("url")
        parsed = urlparse(url)
        if parsed.path.endswith("/linkfilter/"):
            target = parse_qs(parsed.query).get("u", [None])[0]
            if target:
                url = unquote(target)
        quote = re.sub(r"^(?:p>|br>)\s*", "", match.group("quote"), flags=re.IGNORECASE)
        result.append(
            ExternalReview(
                organization=plain_text(match.group("organization")),
                rating=plain_text(match.group("rating")) or None,
                url=_optional_url(url),
                quote=plain_text(quote),
            )
        )
    return result


def parse_external_reviews(
    data: dict[str, Any] | None, html: str | None = None
) -> list[ExternalReview]:
    if not isinstance(data, dict):
        return []
    result: list[ExternalReview] = []
    for raw in _as_list(data.get("external_reviews")):
        if not isinstance(raw, dict) or not raw.get("organization"):
            continue
        result.append(
            ExternalReview(
                organization=str(raw["organization"]),
                rating=raw.get("rating"),
                url=_optional_url(raw.get("url")),
                quote=raw.get("quote"),
            )
        )
    result.extend(parse_external_reviews_html(html))
    unique: list[ExternalReview] = []
    seen: set[tuple[str, str | None, str | None]] = set()
    for item in result:
        key = (item.organization, item.url, item.quote)
        if key not in seen:
            seen.add(key)
            unique.append(item)
    return unique


def parse_store_browse_item(
    payload: dict[str, Any] | None,
    *,
    currency: str | None = None,
    price_region: str | None = None,
    bundle_memberships: dict[int, list[int]] | None = None,
) -> dict[str, Any]:
    """Normalize the public IStoreBrowseService purchase response."""

    if not isinstance(payload, dict):
        return {}
    item: dict[str, Any] = payload
    response = payload.get("response")
    if isinstance(response, dict):
        values = response.get("store_items") or response.get("items")
        if isinstance(values, list) and values and isinstance(values[0], dict):
            item = values[0]
    purchase_options = _as_list(item.get("purchase_options"))
    normalized_currency = (currency or "").upper() or None
    editions: list[EditionInfo] = []
    edition_prices: list[EditionPrice] = []
    bundles: list[Bundle] = []
    bundle_prices: list[BundlePrice] = []
    price_diagnostics: list[dict[str, str]] = []
    for raw in purchase_options:
        if not isinstance(raw, dict):
            continue
        package_id = _parse_int(raw.get("packageid"))
        bundle_id = _parse_int(raw.get("bundleid"))
        name = _edition_name({}, raw)
        recurrence = raw.get("recurrence_info")
        is_subscription = isinstance(recurrence, dict) or raw.get("package_group") == (
            "subscriptions"
        )
        final = _parse_int(raw.get("final_price_in_cents"))
        initial = _parse_int(
            raw.get("original_price_in_cents", raw.get("price_before_bundle_discount"))
        )
        reported_package_discount = _parse_int(raw.get("discount_pct"))
        if initial is None and final is not None and not reported_package_discount:
            initial = final
        package_discount, package_diagnostic = _discount_from_prices(
            initial, final, reported_package_discount
        )
        if package_diagnostic:
            price_diagnostics.append(
                {"code": "price_discount_inconsistent", "message": package_diagnostic}
            )
        static_bundle_discount = _parse_int(
            raw.get(
                "static_discount_pct",
                raw.get("configured_discount_pct", raw.get("bundle_discount_pct")),
            )
        )
        reported_bundle_discount = _parse_int(
            raw.get(
                "effective_discount_pct",
                raw.get("discount_pct", raw.get("bundle_discount_pct")),
            )
        )
        effective_bundle_discount = reported_bundle_discount
        if initial is not None and final is not None and initial > 0:
            effective_bundle_discount = round((initial - final) * 100 / initial)
        bundle_description, bundle_end_at = _discount_metadata(raw)
        if package_id is not None:
            option_currency = raw.get("currency") or normalized_currency
            editions.append(
                EditionInfo(
                    package_id=package_id,
                    name=name,
                )
            )
            period_units = None
            if isinstance(recurrence, dict):
                period_units = _parse_int(recurrence.get("renewal_time_period"))
            edition_prices.append(
                EditionPrice(
                    package_id=package_id,
                    price_region=_price_region(raw)
                    or (
                        price_region.strip().upper()
                        if isinstance(price_region, str) and price_region.strip()
                        else None
                    ),
                    initial=initial,
                    final=final,
                    discount_percent=package_discount,
                    price_type="recurring" if is_subscription else "one_time",
                    period="month" if is_subscription else None,
                    period_units=period_units,
                    discount_description=_discount_metadata(raw)[0],
                    discount_end_at=_discount_metadata(raw)[1],
                    currency=str(option_currency).upper() if option_currency else None,
                )
            )
        if bundle_id is not None:
            option_currency = raw.get("currency") or normalized_currency
            included_ids = list((bundle_memberships or {}).get(bundle_id, []))
            bundles.append(
                Bundle(
                    bundle_id=bundle_id,
                    name=name,
                    discount_percent=static_bundle_discount,
                    must_purchase_as_set=_parse_bool(raw.get("must_purchase_as_set")),
                    edition_package_ids=list(dict.fromkeys(included_ids)),
                )
            )
            bundle_prices.append(
                BundlePrice(
                    bundle_id=bundle_id,
                    price_region=_price_region(raw)
                    or (
                        price_region.strip().upper()
                        if isinstance(price_region, str) and price_region.strip()
                        else None
                    ),
                    effective_discount_percent=effective_bundle_discount,
                    initial=initial,
                    final=final,
                    discount_description=bundle_description,
                    discount_end_at=bundle_end_at,
                    currency=str(option_currency).upper() if option_currency else None,
                )
            )
    return {
        "editions": editions,
        "edition_prices": edition_prices,
        "bundles": bundles,
        "bundle_prices": bundle_prices,
        "media": parse_media(item),
        "supported_languages": parse_store_browse_languages(item.get("supported_languages")),
        "external_links": parse_external_links({"links": item.get("links")}),
        "price_diagnostics": price_diagnostics,
    }


def parse_bundle_membership(payload: dict[str, Any] | None) -> tuple[int | None, list[int]]:
    """Read package membership only from a bundle-specific StoreBrowse item."""

    if not isinstance(payload, dict):
        return None, []
    item = payload
    response = payload.get("response")
    if isinstance(response, dict):
        values = response.get("store_items") or response.get("items")
        if isinstance(values, list) and values and isinstance(values[0], dict):
            item = values[0]
    bundle_id = _parse_int(item.get("id", item.get("bundleid")))
    included = item.get("included_items")
    if not isinstance(included, dict):
        return bundle_id, []
    package_ids: list[int] = []
    for raw in _as_list(included.get("included_packages")):
        if not isinstance(raw, dict):
            continue
        package_id = _parse_int((raw.get("best_purchase_option") or {}).get("packageid"))
        if package_id is not None:
            package_ids.append(package_id)
    return bundle_id, list(dict.fromkeys(package_ids))


def parse_store_browse_languages(value: Any) -> list[LanguageSupport]:
    result: list[LanguageSupport] = []
    for raw in _as_list(value):
        if not isinstance(raw, dict):
            continue
        name = raw.get("language") or raw.get("name")
        if not isinstance(name, str):
            continue
        web_code, steam_language = _language(name)
        result.append(
            LanguageSupport(
                name=name,
                web_code=web_code,
                steam_language=steam_language,
                text=_parse_bool(raw.get("supported", raw.get("interface"))),
                audio=_parse_bool(raw.get("full_audio")),
                subtitles=_parse_bool(raw.get("subtitles")),
            )
        )
    return result


def merge_app_info(
    data: dict[str, Any],
    app_info: dict[str, Any] | None,
    *,
    category_registry: dict[int, str] | None = None,
) -> dict[str, Any]:
    """Overlay public AppInfo fields without replacing localized appdetails."""

    if not isinstance(app_info, dict):
        return data
    common_value = app_info.get("common")
    common: dict[str, Any] = common_value if isinstance(common_value, dict) else {}
    extended_value = app_info.get("extended")
    extended: dict[str, Any] = extended_value if isinstance(extended_value, dict) else {}
    merged = dict(data)
    for key, value in (
        ("type", common.get("type")),
        ("release_state", common.get("releasestate")),
        ("metacritic_score", common.get("metacritic_score")),
        ("metacritic_url", common.get("metacritic_fullurl")),
        ("website", extended.get("homepage")),
        ("header_image", common.get("header_image")),
        ("requiredappid", extended.get("requiredappid")),
    ):
        if value not in (None, ""):
            merged[key] = value
    if isinstance(common, dict):
        semantics = parse_appinfo_semantics(
            common,
            registry=category_registry,
            config=app_info.get("config") if isinstance(app_info.get("config"), dict) else None,
        )
        existing_categories = {
            _field(item, "id"): str(
                _field(item, "name")
                or _field(item, "description")
                or _STEAM_CATEGORY_NAMES.get(_field(item, "id"))
            )
            for item in _as_list(data.get("categories"))
            if _field(item, "id") is not None
        }
        for category in semantics["categories"]:
            category.name = existing_categories.get(
                category.id,
                category.name or _STEAM_CATEGORY_NAMES.get(category.id),
            )
        for category in semantics["accessibility_features"]:
            category.name = existing_categories.get(category.id) or category.name
        merged["categories"] = semantics["categories"]
        merged["accessibility_features"] = semantics["accessibility_features"]
        merged["controllers"] = semantics["controllers"]
        merged["vac_enabled"] = semantics["vac_enabled"]
        merged["gamepad_preferred"] = semantics["gamepad_preferred"]
        merged["controller_support"] = semantics["controller_support"]
        merged["steam_deck"] = semantics["deck_support"]
        merged["deck_support"] = semantics["deck_support"]
    for source_key, target_key in (("developer", "developers"), ("publisher", "publishers")):
        value = extended.get(source_key)
        if isinstance(value, str) and value.strip():
            merged[target_key] = [value.strip()]
    if isinstance(common.get("supported_languages"), dict):
        merged["supported_languages_structured"] = parse_appinfo_languages(
            common["supported_languages"]
        )
    if common.get("eulas") is not None:
        merged["eulas"] = parse_eulas({"eulas": common.get("eulas")})
    merged["app_info"] = app_info
    merged["library_assets"] = common.get("library_assets") or common.get("library_assets_full")
    if "library_assets_full" in common:
        merged["library_assets_full"] = common["library_assets_full"]
    return merged


def parse_app_details(
    data: dict[str, Any],
    locale: LocaleInfo,
    *,
    app_id: int | None = None,
    store_country: str | None = None,
    requirements_data: dict[str, Any] | None = None,
    store_html: str | None = None,
    app_info: dict[str, Any] | None = None,
    store_browse: dict[str, Any] | None = None,
    category_registry: dict[int, str] | None = None,
) -> dict[str, Any]:
    data = merge_app_info(data, app_info, category_registry=category_registry)
    browse_data: dict[str, Any] = {}
    price_overview = data.get("price_overview")
    currency = (
        str(price_overview.get("currency")).strip().upper()
        if isinstance(price_overview, dict) and price_overview.get("currency")
        else None
    )
    raw_package_prices = parse_editions(data, currency=currency)[2]
    raw_bundle_prices = parse_bundles(data, currency=currency)[1]
    if isinstance(store_country, str) and store_country.strip():
        fallback_region = store_country.strip().upper()
        for price in (*raw_package_prices, *raw_bundle_prices):
            if price.price_region is None:
                price.price_region = fallback_region
    if store_browse:
        browse = parse_store_browse_item(
            store_browse,
            currency=currency,
            price_region=store_country,
            bundle_memberships=store_browse.get("_bundle_memberships")
            if isinstance(store_browse, dict)
            else None,
        )
        browse_data = browse
        for key in ("editions", "edition_prices", "bundles", "bundle_prices"):
            if browse.get(key):
                data[key] = browse[key]
        data["media"] = list(data.get("media", [])) + list(browse.get("media", []))
    minimum_date, maximum_date, release_raw, coming_soon = parse_release_window(
        data.get("release_date")
    )
    requirements_source = requirements_data if requirements_data is not None else data
    package_ids, editions, edition_prices = parse_editions(data, currency=currency)
    bundles, bundle_prices = parse_bundles(data, currency=currency)
    if browse_data.get("editions"):
        editions = browse_data["editions"]
        edition_prices = browse_data.get("edition_prices", [])
        package_ids = [item.package_id for item in editions]
        raw_currency_by_package = {
            item.package_id: item.currency for item in raw_package_prices if item.currency
        }
        for price in edition_prices:
            if price.currency is None:
                price.currency = raw_currency_by_package.get(price.package_id)
    if browse_data.get("bundles"):
        bundles = browse_data["bundles"]
        bundle_prices = browse_data.get("bundle_prices", [])
        raw_currency_by_bundle = {
            item.bundle_id: item.currency for item in raw_bundle_prices if item.currency
        }
        for price in bundle_prices:
            if price.currency is None:
                price.currency = raw_currency_by_bundle.get(price.bundle_id)
    if isinstance(store_country, str) and store_country.strip():
        fallback_region = store_country.strip().upper()
        for price in (*edition_prices, *bundle_prices):
            if price.price_region is None:
                price.price_region = fallback_region
    ratings = data.get("ratings") if isinstance(data.get("ratings"), dict) else {}
    full_description = text_value(data.get("detailed_description"))
    about = text_value(data.get("about_the_game"))
    relationship = AppRelationship(
        app_id=app_id,
        demo_id=_parse_int(data.get("demoid", data.get("demo_id"))),
        # ``fullgame`` identifies the parent of a demo.  It is not a DLC
        # relationship and must never be copied into dlc_for_app_id.
        dlc_for_app_id=_parse_int(data.get("dlcforappid")),
        optional_dlc=_parse_bool(data.get("optionaldlc", data.get("optional_dlc"))),
        required_app_id=_parse_int(data.get("requiredappid", data.get("required_appid"))),
    )
    demo_ids = [
        _parse_int(item.get("appid", item.get("demoid")))
        for item in _as_list(data.get("demos"))
        if isinstance(item, dict)
    ]
    dlc_ids = [_parse_int(item) for item in _as_list(data.get("dlc"))]
    media = parse_media(data)
    if store_browse:
        media.extend(browse_data.get("media", []))
    screenshots = [
        item for item in media if isinstance(item, MediaImage) and item.media_type == "screenshot"
    ]
    videos = [item for item in media if isinstance(item, MediaVideo)]
    categories = parse_features(data)
    accessibility_features = parse_accessibility_features(data, store_html)
    if isinstance(data.get("accessibility_features"), list):
        accessibility_features = [
            Feature(
                id=_field(item, "id"),
                name=_field(item, "name") or _field(item, "description"),
            )
            for item in data["accessibility_features"]
            if _field(item, "id") is not None
        ]
    deck_support = parse_steam_deck(data, store_html)
    eulas = (
        data.get("eulas")
        if isinstance(data.get("eulas"), list)
        and all(isinstance(item, ThirdPartyEula) for item in data["eulas"])
        else parse_eulas(
            {"eulas": data.get("eulas")} if isinstance(data.get("eulas"), list) else data,
            store_html,
        )
    )
    controllers = (
        data.get("controllers")
        if isinstance(data.get("controllers"), list)
        else parse_controllers(data, store_html)
    )
    tags, tag_localizations = parse_structured_tags(data, language=locale.requested)
    if not tags:
        tags, tag_localizations = parse_html_structured_tags(store_html, language=locale.requested)
    genres, genre_localizations = parse_structured_genres(data, language=locale.requested)
    workshop = parse_workshop_stats(
        app_info if isinstance(app_info, dict) else data,
        store_html,
        app_id=app_id or 0,
    )
    metacritic = data.get("metacritic") if isinstance(data.get("metacritic"), dict) else {}
    return {
        "localized": LocalizedGameInfo(
            locale=locale.requested,
            steam_language=locale.steam_language,
            store_country=store_country,
            name=data.get("name"),
            short_description=text_value(data.get("short_description")),
            about=about,
            about_the_game=about,
            detailed_description=full_description,
            full_description=full_description or about,
            legal_notice=text_value(data.get("legal_notice")),
        ),
        "type": normalize_steam_type(data.get("type")),
        "source_type": data.get("type"),
        "app_id": app_id,
        "is_free": _parse_bool(data.get("is_free")),
        "vac_enabled": _parse_bool(data.get("vac_enabled")),
        "required_age": _parse_int(data.get("required_age")),
        "external_account_notice": data.get("ext_user_account_notice"),
        "drm_notice": data.get("drm_notice"),
        "price": parse_price(data.get("price_overview")),
        "developers": [
            str(item.get("name"))
            if isinstance(item, dict) and item.get("name") is not None
            else str(item)
            for item in _as_list(data.get("developers"))
        ],
        "publishers": [
            str(item.get("name"))
            if isinstance(item, dict) and item.get("name") is not None
            else str(item)
            for item in _as_list(data.get("publishers"))
        ],
        "organizations": parse_organizations(data),
        "tags": tags,
        "tag_localizations": tag_localizations,
        "genre_rows": genres,
        "genre_localizations": genre_localizations,
        "country_restrictions": parse_country_restrictions(data),
        "workshop_stats": workshop,
        "release_date": minimum_date,
        "release_date_min": minimum_date,
        "release_date_max": maximum_date,
        "release_date_raw": release_raw,
        "coming_soon": coming_soon,
        "release_status": _release_status(data, coming_soon),
        "relationship": relationship,
        "demo_id": relationship.demo_id
        or next((item for item in demo_ids if item is not None), None),
        "dlc_for_app_id": relationship.dlc_for_app_id,
        "optional_dlc": relationship.optional_dlc,
        "required_app_id": relationship.required_app_id,
        "demo_ids": [item for item in demo_ids if item is not None],
        "dlc_ids": [item for item in dlc_ids if item is not None],
        "screenshots": screenshots,
        "videos": videos,
        "media": media,
        "header_image": _optional_url(data.get("header_image")),
        "website": _optional_url(data.get("website")),
        "requirements": RequirementsByOs(
            windows=parse_requirements(requirements_source.get("pc_requirements")),
            mac=parse_requirements(requirements_source.get("mac_requirements")),
            linux=parse_requirements(requirements_source.get("linux_requirements")),
        ),
        "system_requirements": parse_system_requirements(requirements_source),
        "supported_languages": (
            data.get("supported_languages_structured")
            if isinstance(data.get("supported_languages_structured"), list)
            else parse_languages_fallback(data.get("supported_languages"))
        ),
        "platforms": {
            str(key): bool(value)
            for key, value in (data.get("platforms") or {}).items()
            if isinstance(value, bool)
        },
        "categories": categories,
        "features": categories,
        "genres": [
            str(item.get("description"))
            for item in _as_list(data.get("genres"))
            if isinstance(item, dict) and item.get("description")
        ],
        "accessibility_features": accessibility_features,
        "accessibility": accessibility_features,
        "age_ratings": parse_age_ratings(ratings),
        "descriptors": parse_descriptors(data.get("content_descriptors"), ratings),
        "achievements": parse_app_achievements(data, language=locale.requested),
        "editions": editions,
        "edition_prices": edition_prices,
        "package_ids": package_ids,
        "bundles": bundles,
        "bundle_prices": bundle_prices,
        "diagnostics": list(browse_data.get("price_diagnostics", [])),
        "external_links": parse_external_links(data, store_html)
        + list(browse_data.get("external_links", [])),
        "deck_support": deck_support,
        "steam_deck": deck_support,
        "eulas": eulas,
        "controllers": controllers,
        "controller_support": data.get("controller_support") or "none",
        "controller_support_level": _controller_support_level(data.get("controller_support"))
        or "none",
        "gamepad_preferred": _parse_bool(
            data.get("gamepad_preferred", data.get("gamepad_preference"))
        ),
        "branches": parse_build_branches(data),
        "review_language_stats": parse_review_language_stats(
            data.get("review_language_stats", data.get("review_languages"))
        ),
        "external_reviews": parse_external_reviews(data, store_html),
        "metacritic_score": _parse_int(
            metacritic.get("score")
            if isinstance(metacritic, dict)
            else data.get("metacritic_score")
        ),
        "metacritic_url": _optional_url(
            metacritic.get("url") if isinstance(metacritic, dict) else data.get("metacritic_url")
        ),
    }


__all__ = [
    "parse_accessibility_features",
    "parse_achievements",
    "parse_achievement_schema",
    "parse_age_ratings",
    "parse_app_achievements",
    "parse_app_details",
    "parse_bundles",
    "parse_bundle_membership",
    "parse_build_branches",
    "parse_controllers",
    "parse_descriptors",
    "parse_editions",
    "parse_package_metadata",
    "parse_eulas",
    "parse_external_links",
    "parse_external_reviews",
    "parse_external_reviews_html",
    "parse_features",
    "parse_language_table",
    "parse_languages_fallback",
    "parse_media",
    "parse_organizations",
    "parse_structured_tags",
    "parse_html_structured_tags",
    "parse_html_creator_entities",
    "parse_structured_genres",
    "parse_country_restrictions",
    "parse_workshop_stats",
    "parse_price",
    "parse_release_date",
    "parse_release_window",
    "normalize_release_status",
    "parse_requirements",
    "parse_review_language_stats",
    "parse_appinfo_category_ids",
    "parse_appinfo_controller_configs",
    "parse_appinfo_controllers",
    "parse_appinfo_languages",
    "parse_appinfo_semantics",
    "parse_store_browse_item",
    "normalize_steam_type",
    "merge_app_info",
    "parse_steam_deck",
    "parse_system_requirements",
    "parse_tags",
    "plain_text",
    "text_value",
]
