from scraper.steam.locales import normalize_locale
from scraper.steam.parsers import (
    parse_achievements,
    parse_app_details,
    parse_language_table,
    parse_tags,
)


def test_store_html_parsers_extract_tags_languages_and_achievements() -> None:
    html = """
    <div class="glance_tags popular_tags">
      <a class="app_tag" href="/tags/en/Puzzle/">Puzzle</a>
      <a class="app_tag" href="/tags/en/Story%20Rich/">Story Rich</a>
    </div>
    <table class="game_language_options">
      <tr><th>Language</th><th>Interface</th><th>Full Audio</th><th>Subtitles</th></tr>
      <tr><td>English</td><td><span>✓</span></td><td><span>✓</span></td><td><span>✓</span></td></tr>
      <tr><td>Russian</td><td><span>✓</span></td><td></td><td><span>✓</span></td></tr>
    </table>
    <div class="achieveRow">
      <div class="achieveImgHolder"><img src="https://example.com/a.jpg" /></div>
      <div class="achieveTxtHolder"><div class="achievePercent">42.5%</div>
        <div class="achieveTxt"><h3>First Step</h3><h5>Do the thing</h5></div>
      </div>
    </div>
    """
    tags = parse_tags(html)
    languages = parse_language_table(html)
    achievements = parse_achievements(html)

    assert [tag.name for tag in tags] == ["Puzzle", "Story Rich"]
    assert languages[0].full_audio is True
    assert languages[1].full_audio is False
    assert achievements[0].name == "First Step"
    assert achievements[0].global_percent == 42.5
    assert str(achievements[0].icon_url) == "https://example.com/a.jpg"


def test_app_details_preserve_html_and_plain_text() -> None:
    data = {
        "name": "Example",
        "type": "game",
        "short_description": "Short <b>description</b>",
        "detailed_description": "Full <strong>description</strong>",
        "release_date": {"date": "Apr 18, 2011", "coming_soon": False},
        "developers": ["Dev"],
        "publishers": ["Pub"],
        "pc_requirements": {"minimum": "<b>Windows</b> 10"},
        "platforms": {"windows": True, "mac": False, "linux": False},
        "categories": [{"id": 2, "description": "Single-player"}],
        "genres": [{"id": "1", "description": "Action"}],
        "supported_languages": "English<strong>*</strong>, Russian",
    }
    parsed = parse_app_details(data, normalize_locale("en-US"), store_country="kz")
    localized = parsed["localized"]
    assert localized.store_country == "kz"
    assert localized.short_description is not None
    assert localized.short_description.text == "Short description"
    assert localized.short_description.html == "Short <b>description</b>"
    assert localized.full_description is not None
    assert localized.full_description.text == "Full description"
    assert parsed["release_date"].isoformat() == "2011-04-18"
    assert parsed["supported_languages"][0].full_audio is True
