import pytest

from scraper.steam.locales import normalize_locale
from scraper.steam.parsers import (
    parse_achievements,
    parse_app_details,
    parse_build_branches,
    parse_language_table,
    parse_tags,
)
from scraper.steam.reviews import _collect_reviews


def _review_payload(review_id: str, text: str, author_id: str) -> dict[str, object]:
    return {
        "recommendationid": review_id,
        "review": text,
        "voted_up": True,
        "language": "english",
        "votes_up": 10,
        "weighted_vote_score": "0.9",
        "author": {"steamid": author_id},
    }


def _long_review(marker: str) -> str:
    return (
        f"This is a detailed and useful review about gameplay and narrative {marker}. " * 8
    ).strip()


@pytest.mark.asyncio
async def test_review_selection_fetches_next_page_for_minimum_length_and_filters_noise() -> None:
    first_page = [
        _review_payload("1", _long_review("one"), "author-1"),
        _review_payload("2", _long_review("two"), "author-2"),
        _review_payload("3", _long_review("three"), "author-3"),
        _review_payload("duplicate", _long_review("one"), "author-4"),
        _review_payload("ascii-art", "+-+-" * 100, "author-art"),
    ]
    second_page = [_review_payload("4", _long_review("four"), "author-4")]

    class FakeClient:
        cursors: list[str] = []

        async def review_page(self, _app_id, *, language, review_type, cursor):
            del language, review_type
            self.cursors.append(cursor)
            if cursor == "*":
                return {"reviews": first_page, "cursor": "page-2"}
            return {"reviews": second_page, "cursor": "page-3"}

    selected = await _collect_reviews(
        FakeClient(),
        620,
        review_type="positive",
        count=4,
        pages=3,
        minimum_text_length=200,
    )

    assert len(selected) == 4
    assert [review.selection_rank for review in selected] == [1, 2, 3, 4]
    assert all(review.quality_status == "accepted" for review in selected)
    assert all("+-+" not in review.text for review in selected)


@pytest.mark.asyncio
async def test_review_selection_relaxes_length_only_after_pages_are_exhausted() -> None:
    class FakeClient:
        async def review_page(self, _app_id, *, language, review_type, cursor):
            del language, review_type, cursor
            return {
                "reviews": [
                    _review_payload("long", _long_review("long"), "author-1"),
                    _review_payload("short-1", "A short but valid comment.", "author-2"),
                    _review_payload("short-2", "Another short valid comment.", "author-3"),
                ],
                "cursor": "",
            }

    selected = await _collect_reviews(
        FakeClient(),
        620,
        review_type="negative",
        count=4,
        pages=3,
        minimum_text_length=200,
    )

    assert len(selected) == 3
    assert any(review.quality_reason == "minimum_length_relaxed" for review in selected)


@pytest.mark.asyncio
async def test_review_selection_keeps_one_review_per_author() -> None:
    first_page = [
        _review_payload("first", _long_review("first"), "author-1"),
        _review_payload("same-author", _long_review("same-author"), "author-1"),
        _review_payload("second", _long_review("second"), "author-2"),
    ]

    class FakeClient:
        async def review_page(self, _app_id, *, language, review_type, cursor):
            del language, review_type
            if cursor == "*":
                return {"reviews": first_page, "cursor": "page-2"}
            return {
                "reviews": [_review_payload("third", _long_review("third"), "author-3")],
                "cursor": "",
            }

    selected = await _collect_reviews(
        FakeClient(),
        620,
        review_type="positive",
        count=3,
        pages=2,
        minimum_text_length=200,
    )

    assert [review.recommendation_id for review in selected] == [
        "same-author",
        "second",
        "third",
    ]
    assert len({review.author.steam_id for review in selected if review.author}) == 3


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
        "is_free": False,
        "price_overview": {
            "currency": "KZT",
            "initial": 199900,
            "final": 99900,
            "discount_percent": 50,
            "initial_formatted": "",
            "final_formatted": "999 ₸",
        },
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
    assert parsed["is_free"] is False
    assert parsed["price"].currency == "KZT"
    assert parsed["price"].final == 99900
    assert parsed["price"].final_formatted == "999 ₸"


def test_app_details_cover_tz_relationships_media_and_metadata() -> None:
    parsed = parse_app_details(
        {
            "name": "Example",
            "type": "game",
            "release_date": {"date": "Q3 2026", "coming_soon": True},
            "categories": [{"id": 2, "description": "Single-player"}],
            "pc_requirements": {"minimum": "<b>localized</b>"},
            "ratings": {
                "esrb": {
                    "rating": "M",
                    "rating_generated": True,
                    "descriptors": "Violence; Blood",
                }
            },
            "content_descriptors": {"ids": [2], "display_online_notice": True},
            "package_groups": [
                {
                    "name": "Buy Example",
                    "description": "Standard",
                    "subs": [
                        {
                            "packageid": 10,
                            "currency": "USD",
                            "price_in_cents": 1000,
                            "price_in_cents_with_discount": 500,
                            "percent_savings": 50,
                        }
                    ],
                }
            ],
            "bundles": [
                {
                    "bundleid": 20,
                    "name": "Example Bundle",
                    "discount_pct": 10,
                    "price_before_discount": 2000,
                    "price": 1800,
                    "currency": "USD",
                    "item_ids": [10],
                }
            ],
            "movies": [{"id": 3, "dash_h264": "https://cdn.example/trailer.webm"}],
            "deck_compatibility": {"category": 3},
            "eulas": [{"id": 7, "url": "https://example/eula", "name": "ignored"}],
            "website": "https://example.com",
            "controller_support": "Full Controller Support",
            "gamepad_preferred": True,
        },
        normalize_locale("en-US"),
        app_id=42,
        requirements_data={"pc_requirements": {"minimum": "English"}},
    )

    assert parsed["release_date_min"].isoformat() == "2026-07-01"
    assert parsed["release_date_max"].isoformat() == "2026-09-30"
    assert parsed["release_status"] == "not_released"
    assert parsed["relationship"].app_id == 42
    assert parsed["editions"][0].name == "Example"
    assert parsed["edition_prices"][0].final == 500
    assert parsed["bundles"][0].edition_package_ids == []
    assert parsed["requirements"].windows.minimum.text == "English"
    assert parsed["deck_support"].status == "supported"
    assert parsed["eulas"][0].model_dump() == {
        "id": 7,
        "name_description": "ignored",
        "steam_link_support": None,
        "url": "https://example/eula",
        "version": None,
    }
    assert parsed["media"][0].type == "trailer"
    assert parsed["media"][0].format == "webm"
    assert parsed["controller_support_level"] == "full"
    assert parsed["gamepad_preferred"] is True
    assert parsed["age_ratings"][0].raw == "Violence; Blood"


def test_public_appinfo_branches_are_deduplicated_and_private_entries_ignored() -> None:
    branches = parse_build_branches(
        {
            "depots": {
                "branches": {
                    "public": {"buildid": "10", "timeupdated": "100"},
                    "beta": {
                        "buildid": "11",
                        "timeupdated": "200",
                        "description": "Public beta",
                    },
                    "private": {
                        "buildid": "12",
                        "timeupdated": "300",
                        "pwdrequired": "1",
                    },
                }
            }
        }
    )

    assert [(branch.name, branch.build_id) for branch in branches] == [
        ("public", 10),
        ("beta", 11),
    ]
