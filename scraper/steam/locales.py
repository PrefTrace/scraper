import re
from collections.abc import Sequence
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class LocaleInfo:
    requested: str
    web_language: str
    steam_language: str


_LANGUAGES: dict[str, tuple[str, str]] = {
    "ar": ("ar", "arabic"),
    "bg": ("bg", "bulgarian"),
    "cs": ("cs", "czech"),
    "da": ("da", "danish"),
    "de": ("de", "german"),
    "el": ("el", "greek"),
    "en": ("en", "english"),
    "es": ("es", "spanish"),
    "fi": ("fi", "finnish"),
    "fr": ("fr", "french"),
    "hu": ("hu", "hungarian"),
    "id": ("id", "indonesian"),
    "it": ("it", "italian"),
    "ja": ("ja", "japanese"),
    "ko": ("ko", "koreana"),
    "ms": ("ms", "malay"),
    "nl": ("nl", "dutch"),
    "no": ("no", "norwegian"),
    "pl": ("pl", "polish"),
    "pt": ("pt", "portuguese"),
    "ro": ("ro", "romanian"),
    "ru": ("ru", "russian"),
    "sv": ("sv", "swedish"),
    "th": ("th", "thai"),
    "tr": ("tr", "turkish"),
    "uk": ("uk", "ukrainian"),
    "vi": ("vi", "vietnamese"),
    "zh": ("zh-CN", "schinese"),
}

_REGIONAL_LANGUAGE_OVERRIDES: dict[str, tuple[str, str]] = {
    "zh-cn": ("zh-CN", "schinese"),
    "zh-tw": ("zh-TW", "tchinese"),
    "es-419": ("es-419", "latam"),
    "pt-br": ("pt-BR", "brazilian"),
}

# These mappings intentionally have separate directions. Steam AppInfo uses
# source language names, while Store/API payloads may expose Steam language
# codes. Neither direction should be used to guess the other vocabulary.
STEAM_LANGUAGE_CODE_TO_BCP47: dict[str, str] = {
    "arabic": "ar",
    "bulgarian": "bg",
    "czech": "cs",
    "danish": "da",
    "dutch": "nl",
    "english": "en",
    "finnish": "fi",
    "french": "fr",
    "german": "de",
    "greek": "el",
    "hungarian": "hu",
    "indonesian": "id",
    "italian": "it",
    "japanese": "ja",
    "koreana": "ko",
    "malay": "ms",
    "norwegian": "no",
    "polish": "pl",
    "portuguese": "pt",
    "brazilian": "pt-BR",
    "romanian": "ro",
    "russian": "ru",
    "schinese": "zh-CN",
    "spanish": "es",
    "latam": "es-419",
    "swedish": "sv",
    "thai": "th",
    "tchinese": "zh-TW",
    "turkish": "tr",
    "ukrainian": "uk",
    "vietnamese": "vi",
}

STEAM_LANGUAGE_NAME_TO_CODE: dict[str, str] = {
    "arabic": "arabic",
    "bulgarian": "bulgarian",
    "czech": "czech",
    "danish": "danish",
    "dutch": "dutch",
    "english": "english",
    "finnish": "finnish",
    "french": "french",
    "german": "german",
    "greek": "greek",
    "hungarian": "hungarian",
    "indonesian": "indonesian",
    "italian": "italian",
    "japanese": "japanese",
    "korean": "koreana",
    "koreana": "koreana",
    "malay": "malay",
    "norwegian": "norwegian",
    "polish": "polish",
    "portuguese": "portuguese",
    "portuguese - portugal": "portuguese",
    "portuguese - brazil": "brazilian",
    "brazilian": "brazilian",
    "romanian": "romanian",
    "russian": "russian",
    "simplified chinese": "schinese",
    "schinese": "schinese",
    "spanish": "spanish",
    "spanish - spain": "spanish",
    "spanish - latin america": "latam",
    "swedish": "swedish",
    "thai": "thai",
    "traditional chinese": "tchinese",
    "tchinese": "tchinese",
    "turkish": "turkish",
    "ukrainian": "ukrainian",
    "vietnamese": "vietnamese",
}


def steam_code_to_bcp47(value: str) -> str | None:
    return STEAM_LANGUAGE_CODE_TO_BCP47.get(value.strip().casefold())


def steam_name_to_code(value: str) -> str | None:
    return STEAM_LANGUAGE_NAME_TO_CODE.get(canonicalize_language_name(value))


def normalize_steam_language(value: str) -> str | None:
    """Return BCP47 for either a known Steam code or display language name."""

    code = steam_code_to_bcp47(value)
    if code is not None:
        return code
    steam_language = steam_name_to_code(value)
    return steam_code_to_bcp47(steam_language) if steam_language else None


def normalize_locale(value: str) -> LocaleInfo:
    normalized = value.strip().replace("_", "-")
    if not normalized:
        raise ValueError("Locale must not be empty")
    key = normalized.lower()

    if key in _REGIONAL_LANGUAGE_OVERRIDES:
        web_language, steam_language = _REGIONAL_LANGUAGE_OVERRIDES[key]
        return LocaleInfo(normalized, web_language, steam_language)

    language = key.split("-", maxsplit=1)[0]
    try:
        web_language, steam_language = _LANGUAGES[language]
    except KeyError as exc:
        raise ValueError(f"Unsupported Steam locale: {value!r}") from exc

    return LocaleInfo(normalized, web_language, steam_language)


def normalize_store_country(value: str | None) -> str | None:
    if value is None:
        return None
    normalized = value.strip().lower()
    if not re.fullmatch(r"[a-z]{2}", normalized):
        raise ValueError("Store country must be an ISO 3166-1 alpha-2 code")
    return normalized


def normalize_locales(values: Sequence[str] | None) -> list[LocaleInfo]:
    raw_values = list(values or ["en-US"])
    if not raw_values:
        raise ValueError("At least one locale is required")
    result: list[LocaleInfo] = []
    seen: set[str] = set()
    for value in raw_values:
        locale = normalize_locale(value)
        dedupe_key = locale.requested.lower()
        if dedupe_key not in seen:
            result.append(locale)
            seen.add(dedupe_key)
    return result


def canonicalize_language_name(name: str) -> str:
    return re.sub(r"\s+", " ", name.strip()).casefold()
