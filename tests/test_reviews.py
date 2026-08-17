import pytest

from scraper.steam.reviews import collect_reviews


class FakeSteamClient:
    def __init__(self) -> None:
        self.calls: list[str] = []

    async def review_page(
        self,
        app_id: int,
        *,
        language: str,
        review_type: str,
        cursor: str,
    ) -> dict:
        self.calls.append(cursor)
        return {
            "success": 1,
            "cursor": "next",
            "reviews": [
                {
                    "recommendationid": "1",
                    "review": "short",
                    "voted_up": review_type == "positive",
                    "language": "english",
                    "votes_up": 10,
                    "weighted_vote_score": "0.8",
                },
                {
                    "recommendationid": "2",
                    "review": "a much longer and more detailed review",
                    "voted_up": review_type == "positive",
                    "language": "english",
                    "votes_up": 2,
                    "weighted_vote_score": "0.2",
                },
            ],
        }


@pytest.mark.asyncio
async def test_reviews_choose_longest_from_bounded_steam_window() -> None:
    client = FakeSteamClient()
    reviews = await collect_reviews(
        client, 620, review_type="positive", count=1, pages=1
    )
    assert len(reviews) == 1
    assert reviews[0].recommendation_id == "2"
    assert client.calls == ["*"]
