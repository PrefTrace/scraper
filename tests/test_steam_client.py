from scraper.steam.client import SteamClient
from scraper.steam.locales import normalize_locale


def test_store_country_is_not_derived_from_requested_language() -> None:
    locale = normalize_locale("ru-RU")

    assert SteamClient._localized_params(locale, "kz") == {"cc": "kz", "l": "ru"}
    assert SteamClient._localized_params(locale, None) == {"l": "ru"}
