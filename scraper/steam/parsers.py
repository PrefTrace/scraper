"""Small, defensive parsers for the Steam Store and community responses.

Steam has no single stable schema for all of the data exposed on an app page.
The functions in this module keep the source vocabulary visible and avoid
throwing away fields just because a particular app does not have them.
"""

from __future__ import annotations

import calendar
import re
from datetime import UTC, date, datetime
from typing import Any
from urllib.parse import urlparse

from selectolax.parser import HTMLParser, Node

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
    TextValue,
    ThirdPartyEula,
)

from .locales import LocaleInfo, canonicalize_language_name

_STEAM_LANGUAGE_NAMES: dict[str, tuple[str, str]] = {
    "arabic": ("ar", "arabic"),
    "bulgarian": ("bg", "bulgarian"),
    "czech": ("cs", "czech"),
    "danish": ("da", "danish"),
    "dutch": ("nl", "dutch"),
    "english": ("en", "english"),
    "finnish": ("fi", "finnish"),
    "french": ("fr", "french"),
    "german": ("de", "german"),
    "greek": ("el", "greek"),
    "hungarian": ("hu", "hungarian"),
    "indonesian": ("id", "indonesian"),
    "italian": ("it", "italian"),
    "japanese": ("ja", "japanese"),
    "korean": ("ko", "koreana"),
    "malay": ("ms", "malay"),
    "norwegian": ("no", "norwegian"),
    "polish": ("pl", "polish"),
    "portuguese - portugal": ("pt", "portuguese"),
    "portuguese - brazil": ("pt-BR", "brazilian"),
    "romanian": ("ro", "romanian"),
    "russian": ("ru", "russian"),
    "simplified chinese": ("zh-CN", "schinese"),
    "spanish - spain": ("es", "spanish"),
    "spanish - latin america": ("es-419", "latam"),
    "swedish": ("sv", "swedish"),
    "thai": ("th", "thai"),
    "traditional chinese": ("zh-TW", "tchinese"),
    "turkish": ("tr", "turkish"),
    "ukrainian": ("uk", "ukrainian"),
    "vietnamese": ("vi", "vietnamese"),
}

_CONTENT_DESCRIPTOR_NAMES = {
    1: "mild language",
    2: "violence",
    3: "blood",
    4: "nudity",
    5: "sexual content",
    6: "strong language",
    7: "mature humor",
    8: "drugs",
    9: "horror",
    10: "gambling",
    11: "general mature content",
}

_MEDIA_KEY_TYPES = {
    "header_image": "header_capsule",
    "capsule_image": "main_capsule",
    "capsule_imagev5": "main_capsule",
    "capsule_imagev6": "main_capsule",
    "small_capsule": "small_capsule",
    "vertical_capsule": "vertical_capsule",
    "background": "page_background",
    "background_raw": "page_background",
}


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
    return value if value.startswith(("http://", "https://")) else None


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
        ("facebook.", "facebook"),
        ("twitter.", "twitter"),
        ("x.com", "x"),
        ("discord.", "discord"),
        ("youtube.", "youtube"),
        ("twitch.", "twitch"),
        ("instagram.", "instagram"),
        ("reddit.", "reddit"),
        ("vk.com", "vk"),
    )
    for marker, link_type in known_hosts:
        if host.startswith(marker) or host == marker.rstrip("."):
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
        if normalized in {"preorder", "pre_order"}:
            return "preorder"
        if normalized in {"early_access", "earlyaccess"}:
            return "early_access"
    if _parse_bool(data.get("is_early_access")) or _parse_bool(data.get("early_access")):
        return "early_access"
    if _parse_bool(data.get("is_preorder")) or _parse_bool(data.get("preorder")):
        return "preorder"
    return "not_released" if coming_soon is True else "released"


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
    match = _STEAM_LANGUAGE_NAMES.get(canonicalize_language_name(value))
    return match if match else (None, None)


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
                interface=True,
                subtitles=True,
                full_audio="<strong>" in raw_part.lower() or raw_part.rstrip().endswith("*"),
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
                interface=bool(cells[1].css_first("span")),
                full_audio=bool(cells[2].css_first("span")),
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


def _achievement_from_data(
    raw: dict[str, Any], *, language: str | None = None
) -> Achievement | None:
    name = raw.get("name") or raw.get("displayName") or raw.get("display_name")
    if not isinstance(name, str) or not name.strip():
        return None
    return Achievement(
        api_name=(str(raw["apiname"]) if raw.get("apiname") is not None else None),
        name=name.strip(),
        description=(str(raw["description"]) if raw.get("description") is not None else None),
        global_percent=_parse_float(raw.get("global_percent", raw.get("percent"))),
        hidden=_parse_bool(raw.get("hidden")),
        icon_url=_optional_url(raw.get("icon") or raw.get("icon_url")),
        language=language,
        steam_id=_parse_int(raw.get("id", raw.get("achievementid"))),
    )


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
        api_name = item.get("apiname")
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
        achievement_id = row.attributes.get("data-achievementid") or row.attributes.get(
            "data-achievement-id"
        )
        api_name = row.attributes.get("data-apiname") or row.attributes.get("data-achievement")
        result.append(
            Achievement(
                api_name=api_name,
                name=name,
                description=description,
                global_percent=float(percent_match.group(1)) if percent_match else None,
                hidden="hidden" in class_name.casefold(),
                icon_url=_optional_url(icon.attributes.get("src") if icon else None),
                language=language,
                steam_id=_parse_int(achievement_id),
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
                language=language,
            )
        )

    for key, media_type in _MEDIA_KEY_TYPES.items():
        url = _optional_url(data.get(key))
        if url:
            result.append(
                MediaImage(
                    media_type=media_type,
                    type=media_type,
                    url=url,
                    full_url=url,
                    format=_format_from_url(url),
                    language=language,
                )
            )

    assets = data.get("library_assets")
    if isinstance(assets, dict):
        for key, raw in assets.items():
            if isinstance(raw, dict):
                url = _optional_url(raw.get("image") or raw.get("url") or raw.get("filename"))
            else:
                url = _optional_url(raw)
            if url:
                result.append(
                    MediaImage(
                        media_type=str(key),
                        type=str(key),
                        url=url,
                        full_url=url,
                        format=_format_from_url(url),
                        language=language,
                    )
                )

    for item in _as_list(data.get("movies")):
        if not isinstance(item, dict):
            continue
        urls = [
            _optional_url(item.get("hls_h264")),
            _optional_url(item.get("dash_h264")),
            _optional_url(item.get("dash_av1")),
        ]
        primary_url = next((url for url in (urls[2], urls[1], urls[0]) if url), None)
        result.append(
            MediaVideo(
                id=_parse_int(item.get("id")),
                name=item.get("name") if isinstance(item.get("name"), str) else None,
                thumbnail_url=_optional_url(item.get("thumbnail")),
                dash_av1_url=urls[2],
                dash_h264_url=urls[1],
                hls_h264_url=urls[0],
                highlight=_parse_bool(item.get("highlight")),
                media_type="trailer",
                type="trailer",
                format=_format_from_url(primary_url),
                language=language,
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
    for value in (item.get("name"), item.get("option_text"), group.get("name")):
        if isinstance(value, str) and value.strip():
            name = plain_text(value).strip()
            return re.sub(r"^(?:buy|purchase)\s+", "", name, flags=re.IGNORECASE).strip()
    return None


def parse_editions(data: dict[str, Any]) -> tuple[list[int], list[EditionInfo], list[EditionPrice]]:
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
        currency = item.get("currency", group.get("currency"))
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
                currency=str(currency) if currency is not None else None,
                initial=initial,
                final=final,
                discount_percent=_parse_int(
                    item.get("percent_savings", group.get("percent_savings"))
                ),
                price_type="recurring"
                if _parse_bool(
                    item.get(
                        "is_recurring_subscription", group.get("is_recurring_subscription")
                    )
                )
                else "one_time",
                period=(
                    str(recurring["period"]) if recurring.get("period") is not None else None
                ),
                period_units=_parse_int(recurring.get("frequency", recurring.get("period_units"))),
            )
        )
    return package_ids, editions, prices


def parse_bundles(data: dict[str, Any]) -> tuple[list[Bundle], list[BundlePrice]]:
    bundles: list[Bundle] = []
    prices: list[BundlePrice] = []
    for raw in _as_list(data.get("bundles")):
        if not isinstance(raw, dict):
            continue
        bundle_id = _parse_int(raw.get("bundleid", raw.get("bundle_id", raw.get("id"))))
        if bundle_id is None:
            continue
        edition_ids: list[int] = []
        for item in _as_list(raw.get("items")) + _as_list(raw.get("item_ids")):
            raw_id = (
                item.get("packageid", item.get("package_id", item.get("id")))
                if isinstance(item, dict)
                else item
            )
            parsed_id = _parse_int(raw_id)
            if parsed_id is not None:
                edition_ids.append(parsed_id)
        bundles.append(
            Bundle(
                bundle_id=bundle_id,
                name=raw.get("name") if isinstance(raw.get("name"), str) else None,
                discount_percent=_parse_int(raw.get("discount_pct", raw.get("discount_percent"))),
                must_purchase_as_set=_parse_bool(raw.get("must_purchase_as_set")),
                edition_package_ids=list(dict.fromkeys(edition_ids)),
            )
        )
        currency = raw.get("currency")
        initial = _parse_int(raw.get("price_before_discount", raw.get("initial")))
        final = _parse_int(raw.get("price", raw.get("final")))
        prices.append(
            BundlePrice(
                bundle_id=bundle_id,
                currency=str(currency) if currency is not None else None,
                discount_percent=_parse_int(
                    raw.get("discount_pct", raw.get("discount_percent"))
                ),
                initial=initial,
                final=final,
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
    unique: list[ExternalLink] = []
    seen: set[tuple[str, str | None, str | None]] = set()
    for link in result:
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
        descriptors = [
            item.strip() for item in re.split(r"[;,\n]+", raw_descriptors) if item.strip()
        ]
        result.append(
            AgeRating(
                authority=str(authority),
                age_id=str(authority),
                rating=str(raw["rating"]) if raw.get("rating") is not None else None,
                required_age=_parse_int(raw.get("required_age")),
                descriptors=descriptors,
                banned=_parse_bool(raw.get("banned")),
                use_age_gate=_parse_bool(raw.get("use_age_gate")),
                rating_generated=_parse_bool(raw.get("rating_generated")),
                raw=raw_descriptors or None,
            )
        )
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
                        name=_CONTENT_DESCRIPTOR_NAMES.get(descriptor_id, str(descriptor_id)),
                    )
                )
        notes = data.get("notes")
        if isinstance(notes, str) and notes.strip():
            result.append(Descriptor(age_id="steam", name=notes.strip()))
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
            for descriptor in re.split(r"[;,\n]+", str(raw.get("descriptors", ""))):
                descriptor = descriptor.strip()
                if descriptor:
                    result.append(Descriptor(age_id=str(authority), name=descriptor))
    unique: list[Descriptor] = []
    seen: set[tuple[str, int | None, str]] = set()
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
    seen: set[tuple[int | None, str]] = set()
    for feature in result:
        key = (feature.id, feature.name)
        if key not in seen:
            seen.add(key)
            unique.append(feature)
    return unique


def _deck_status(raw: Any) -> str:
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
        eula_values = _as_list(raw_eulas) or ([raw_eulas] if isinstance(raw_eulas, dict) else [])
        for raw in eula_values:
            if not isinstance(raw, dict):
                continue
            result.append(
                ThirdPartyEula(
                    id=_parse_int(raw.get("id", raw.get("eulaid"))),
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
                        id=_parse_int(node.attributes.get("data-eula-id")),
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


def parse_organizations(data: dict[str, Any] | None) -> list[OrganizationCredit]:
    if not isinstance(data, dict):
        return []
    result: list[OrganizationCredit] = []
    for key, status in (("developers", "developer"), ("publishers", "publisher")):
        for raw in _as_list(data.get(key)):
            if isinstance(raw, dict):
                name = raw.get("name")
                organization_id = _parse_int(raw.get("id"))
            else:
                name, organization_id = raw, None
            if isinstance(name, str) and name.strip():
                result.append(
                    OrganizationCredit(name=name.strip(), status=status, id=organization_id)
                )
    return result


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
        result.append(
            BuildBranch(
                name=branch_name,
                updated_at=(datetime.fromtimestamp(updated, tz=UTC) if updated else None),
                description=raw.get("description"),
                build_id=_parse_int(raw.get("buildid", raw.get("build_id"))),
                download_size=_parse_int(
                    raw.get("download_size", raw.get("downloadsize", raw.get("size")))
                ),
                disk_size=_parse_int(raw.get("disk_size", raw.get("disksize"))),
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
            web_code, _steam_language = _language(language)
            language = web_code or language
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


def parse_external_reviews(data: dict[str, Any] | None) -> list[ExternalReview]:
    if not isinstance(data, dict):
        return []
    result: list[ExternalReview] = []
    metacritic = data.get("metacritic")
    if isinstance(metacritic, dict):
        result.append(
            ExternalReview(
                organization="metacritic",
                rating=str(metacritic.get("score"))
                if metacritic.get("score") is not None
                else None,
                url=_optional_url(metacritic.get("url")),
            )
        )
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
    return result


def parse_app_details(
    data: dict[str, Any],
    locale: LocaleInfo,
    *,
    app_id: int | None = None,
    store_country: str | None = None,
    requirements_data: dict[str, Any] | None = None,
    store_html: str | None = None,
) -> dict[str, Any]:
    minimum_date, maximum_date, release_raw, coming_soon = parse_release_window(
        data.get("release_date")
    )
    requirements_source = requirements_data if requirements_data is not None else data
    package_ids, editions, edition_prices = parse_editions(data)
    bundles, bundle_prices = parse_bundles(data)
    ratings = data.get("ratings") if isinstance(data.get("ratings"), dict) else {}
    full_description = text_value(data.get("detailed_description"))
    about = text_value(data.get("about_the_game"))
    parent: dict[str, Any] = {}
    parent_value = data.get("fullgame")
    if isinstance(parent_value, dict):
        parent.update(parent_value)
    relationship = AppRelationship(
        app_id=app_id,
        demo_id=_parse_int(data.get("demoid", data.get("demo_id"))),
        dlc_for_app_id=_parse_int(data.get("dlcforappid", parent.get("appid"))),
        optional_dlc=_parse_bool(data.get("optionaldlc", data.get("optional_dlc"))),
        required_app_id=_parse_int(data.get("requiredappid", data.get("required_appid"))),
    )
    demo_ids = [
        _parse_int(item.get("appid", item.get("demoid")))
        for item in _as_list(data.get("demos"))
        if isinstance(item, dict)
    ]
    dlc_ids = [_parse_int(item) for item in _as_list(data.get("dlc"))]
    media = parse_media(data, language=locale.requested)
    screenshots = [
        item for item in media if isinstance(item, MediaImage) and item.media_type == "screenshot"
    ]
    videos = [item for item in media if isinstance(item, MediaVideo)]
    categories = parse_features(data)
    accessibility_features = parse_accessibility_features(data, store_html)
    deck_support = parse_steam_deck(data, store_html)
    eulas = parse_eulas(data, store_html)
    controllers = parse_controllers(data, store_html)
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
        "type": data.get("type"),
        "app_id": app_id,
        "is_free": _parse_bool(data.get("is_free")),
        "vac_enabled": _parse_bool(data.get("vac_enabled")),
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
        "release_date": minimum_date,
        "release_date_min": minimum_date,
        "release_date_max": maximum_date,
        "release_date_raw": release_raw,
        "coming_soon": coming_soon,
        "release_status": _release_status(data, coming_soon),
        "relationship": relationship,
        "demo_id": relationship.demo_id,
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
        "supported_languages": parse_languages_fallback(data.get("supported_languages")),
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
        "external_links": parse_external_links(data, store_html),
        "deck_support": deck_support,
        "steam_deck": deck_support,
        "eulas": eulas,
        "controllers": controllers,
        "controller_support": data.get("controller_support"),
        "controller_support_level": _controller_support_level(data.get("controller_support")),
        "gamepad_preferred": _parse_bool(
            data.get("gamepad_preferred", data.get("gamepad_preference"))
        ),
        "branches": parse_build_branches(data),
        "review_language_stats": parse_review_language_stats(
            data.get("review_language_stats", data.get("review_languages"))
        ),
        "external_reviews": parse_external_reviews(data),
        "metacritic_score": _parse_int(
            metacritic.get("score") if isinstance(metacritic, dict) else None
        ),
        "metacritic_url": _optional_url(
            metacritic.get("url") if isinstance(metacritic, dict) else None
        ),
        "metacritic_name": (
            metacritic.get("name") if isinstance(metacritic, dict) else data.get("metacritic_name")
        ),
    }


__all__ = [
    "parse_accessibility_features",
    "parse_achievements",
    "parse_age_ratings",
    "parse_app_achievements",
    "parse_app_details",
    "parse_bundles",
    "parse_build_branches",
    "parse_controllers",
    "parse_descriptors",
    "parse_editions",
    "parse_eulas",
    "parse_external_links",
    "parse_external_reviews",
    "parse_features",
    "parse_language_table",
    "parse_languages_fallback",
    "parse_media",
    "parse_organizations",
    "parse_price",
    "parse_release_date",
    "parse_release_window",
    "parse_requirements",
    "parse_review_language_stats",
    "parse_steam_deck",
    "parse_system_requirements",
    "parse_tags",
    "plain_text",
    "text_value",
]
