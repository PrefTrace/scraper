import pytest

from scraper.api import extract_app_id
from scraper.steam.locales import normalize_locale, normalize_locales, normalize_store_country


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


def test_invalid_locale_and_url_are_rejected() -> None:
    with pytest.raises(ValueError):
        normalize_locale("xx-XX")
    with pytest.raises(ValueError):
        extract_app_id("https://steamcommunity.com/app/620/")
    assert extract_app_id("https://store.steampowered.com/app/620/Portal_2/?cc=us") == 620
