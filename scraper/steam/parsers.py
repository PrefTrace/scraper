from __future__ import annotations

import re
from datetime import date, datetime
from typing import Any

from selectolax.parser import HTMLParser, Node

from scraper.models import (
    Achievement,
    AgeRating,
    Category,
    LanguageSupport,
    LocalizedGameInfo,
    MediaImage,
    MediaVideo,
    PriceOverview,
    Requirements,
    RequirementsByOs,
    Tag,
    TextValue,
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
    if isinstance(value, str) and value.startswith(("http://", "https://")):
        return value
    return None


def _parse_int(value: Any) -> int | None:
    try:
        return int(value) if value not in (None, "") else None
    except (TypeError, ValueError):
        return None


def _parse_bool(value: Any) -> bool | None:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    if isinstance(value, str):
        normalized = value.strip().casefold()
        if normalized in {"1", "true", "yes"}:
            return True
        if normalized in {"0", "false", "no"}:
            return False
    return None


def parse_release_date(value: dict[str, Any] | None) -> tuple[date | None, str | None, bool | None]:
    if not value:
        return None, None, None
    raw = value.get("date")
    coming_soon = value.get("coming_soon")
    if not isinstance(raw, str) or not raw.strip():
        return None, raw if isinstance(raw, str) else None, coming_soon
    candidate = raw.strip()
    for fmt in ("%b %d, %Y", "%B %d, %Y", "%d %b, %Y", "%Y-%m-%d"):
        try:
            return datetime.strptime(candidate, fmt).date(), candidate, coming_soon
        except ValueError:
            continue
    match = re.search(r"(\d{4})", candidate)
    if match:
        return date(int(match.group(1)), 1, 1), candidate, coming_soon
    return None, candidate, coming_soon


def parse_requirements(data: dict[str, Any] | None) -> Requirements | None:
    if not isinstance(data, dict):
        return None
    minimum = text_value(data.get("minimum"))
    recommended = text_value(data.get("recommended"))
    if minimum is None and recommended is None:
        return None
    return Requirements(minimum=minimum, recommended=recommended)


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
            data["initial_formatted"]
            if isinstance(data.get("initial_formatted"), str)
            else None
        ),
        final_formatted=(
            data["final_formatted"]
            if isinstance(data.get("final_formatted"), str)
            else None
        ),
    )


def parse_languages_fallback(value: str | None) -> list[LanguageSupport]:
    if not value:
        return []
    result: list[LanguageSupport] = []
    for raw_part in re.split(r",\s*", value):
        part = plain_text(raw_part).strip().rstrip("*")
        if not part or "languages with" in part.casefold():
            continue
        full_audio = "<strong>" in raw_part.lower() or raw_part.rstrip().endswith("*")
        match = _STEAM_LANGUAGE_NAMES.get(canonicalize_language_name(part))
        result.append(
            LanguageSupport(
                name=part,
                web_code=match[0] if match else None,
                steam_language=match[1] if match else None,
                interface=True,
                subtitles=True,
                full_audio=full_audio,
            )
        )
    return result


def parse_language_table(html: str) -> list[LanguageSupport]:
    parser = HTMLParser(html)
    table = parser.css_first("table.game_language_options")
    if table is None:
        return []
    result: list[LanguageSupport] = []
    rows = table.css("tr")
    for row in rows[1:]:
        cells = row.css("td")
        if len(cells) < 4:
            continue
        name = _node_text(cells[0])
        if not name:
            continue
        match = _STEAM_LANGUAGE_NAMES.get(canonicalize_language_name(name))
        result.append(
            LanguageSupport(
                name=name,
                web_code=match[0] if match else None,
                steam_language=match[1] if match else None,
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


def parse_achievements(html: str) -> list[Achievement]:
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
        result.append(
            Achievement(
                name=name,
                description=description,
                global_percent=float(percent_match.group(1)) if percent_match else None,
                hidden="hidden" in class_name.casefold(),
                icon_url=_optional_url(icon.attributes.get("src") if icon else None),
            )
        )
    return result


def parse_app_details(
    data: dict[str, Any],
    locale: LocaleInfo,
    *,
    store_country: str | None = None,
) -> dict[str, Any]:
    release_date, release_raw, coming_soon = parse_release_date(data.get("release_date"))
    screenshots = [
        MediaImage(
            id=item.get("id"),
            thumbnail_url=_optional_url(item.get("path_thumbnail")),
            full_url=_optional_url(item.get("path_full")),
        )
        for item in data.get("screenshots", [])
        if isinstance(item, dict)
    ]
    videos = [
        MediaVideo(
            id=item.get("id"),
            name=item.get("name"),
            thumbnail_url=_optional_url(item.get("thumbnail")),
            dash_av1_url=_optional_url(item.get("dash_av1")),
            dash_h264_url=_optional_url(item.get("dash_h264")),
            hls_h264_url=_optional_url(item.get("hls_h264")),
            highlight=item.get("highlight"),
        )
        for item in data.get("movies", [])
        if isinstance(item, dict)
    ]
    return {
        "localized": LocalizedGameInfo(
            locale=locale.requested,
            steam_language=locale.steam_language,
            store_country=store_country,
            name=data.get("name"),
            short_description=text_value(data.get("short_description")),
            full_description=text_value(
                data.get("detailed_description") or data.get("about_the_game")
            ),
        ),
        "type": data.get("type"),
        "is_free": _parse_bool(data.get("is_free")),
        "price": parse_price(data.get("price_overview")),
        "developers": [str(item) for item in data.get("developers", [])],
        "publishers": [str(item) for item in data.get("publishers", [])],
        "release_date": release_date,
        "release_date_raw": release_raw,
        "coming_soon": coming_soon,
        "screenshots": screenshots,
        "videos": videos,
        "header_image": _optional_url(data.get("header_image")),
        "website": _optional_url(data.get("website")),
        "requirements": RequirementsByOs(
            windows=parse_requirements(data.get("pc_requirements")),
            mac=parse_requirements(data.get("mac_requirements")),
            linux=parse_requirements(data.get("linux_requirements")),
        ),
        "supported_languages": parse_languages_fallback(data.get("supported_languages")),
        "platforms": {
            str(key): bool(value)
            for key, value in (data.get("platforms") or {}).items()
            if isinstance(value, bool)
        },
        "categories": [
            Category(id=item.get("id"), name=str(item.get("description", "")))
            for item in data.get("categories", [])
            if isinstance(item, dict) and item.get("description")
        ],
        "genres": [
            str(item.get("description"))
            for item in data.get("genres", [])
            if isinstance(item, dict) and item.get("description")
        ],
        "age_ratings": parse_age_ratings(data.get("ratings")),
    }


def parse_age_ratings(data: dict[str, Any] | None) -> list[AgeRating]:
    if not isinstance(data, dict):
        return []
    result: list[AgeRating] = []
    for authority, raw in data.items():
        if not isinstance(raw, dict):
            continue
        descriptors = re.split(r"[;\n]+", str(raw.get("descriptors", "")))
        result.append(
            AgeRating(
                authority=str(authority),
                rating=str(raw["rating"]) if raw.get("rating") is not None else None,
                required_age=_parse_int(raw.get("required_age")),
                descriptors=[item.strip() for item in descriptors if item.strip()],
                banned=(
                    _parse_bool(raw["banned"])
                    if raw.get("banned") is not None
                    else None
                ),
                use_age_gate=(
                    _parse_bool(raw["use_age_gate"])
                    if raw.get("use_age_gate") is not None
                    else None
                ),
            )
        )
    return result
