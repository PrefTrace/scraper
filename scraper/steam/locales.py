import re
from collections.abc import Sequence
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class LocaleInfo:
    requested: str
    web_language: str
    steam_language: str


DEFAULT_STORE_COUNTRY = "kz"


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
