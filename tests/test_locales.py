import pytest

from scraper.steam.locales import (
    normalize_locale,
    normalize_locales,
    normalize_steam_language,
    normalize_store_country,
    steam_code_to_bcp47,
    steam_name_to_code,
)


def test_locale_mapping_supports_regions() -> None:
    locale = normalize_locale("ru-RU")
    assert locale.web_language == "ru"
    assert locale.steam_language == "russian"

    brazil = normalize_locale("pt-BR")
    assert brazil.web_language == "pt-BR"
    assert brazil.steam_language == "brazilian"


def test_store_country_is_separate_from_language() -> None:
    assert normalize_store_country("KZ") == "kz"
    assert normalize_store_country(None) is None
    with pytest.raises(ValueError):
        normalize_store_country("russian")


def test_locales_are_deduplicated() -> None:
    result = normalize_locales(["en-US", "en-US", "ru-RU"])
    assert [item.requested for item in result] == ["en-US", "ru-RU"]


def test_invalid_locale_is_rejected() -> None:
    with pytest.raises(ValueError):
        normalize_locale("xx-XX")


def test_steam_language_mapping_keeps_code_and_display_name_directions_separate() -> None:
    assert steam_code_to_bcp47("spanish") == "es"
    assert steam_code_to_bcp47("latam") == "es-419"
    assert steam_code_to_bcp47("portuguese") == "pt"
    assert steam_code_to_bcp47("tchinese") == "zh-TW"
    assert steam_code_to_bcp47("schinese") == "zh-CN"
    assert steam_code_to_bcp47("brazilian") == "pt-BR"
    assert steam_code_to_bcp47("koreana") == "ko"
    assert steam_name_to_code("Spanish - Latin America") == "latam"
    assert steam_name_to_code("Portuguese - Brazil") == "brazilian"
    assert normalize_steam_language("Traditional Chinese") == "zh-TW"
    assert normalize_steam_language("unknown-steam-language") is None
