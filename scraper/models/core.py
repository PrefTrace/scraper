from datetime import date, datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from scraper.diagnostics import Diagnostic


class Model(BaseModel):
    model_config = ConfigDict(extra="ignore")


class TextValue(Model):
    text: str = ""
    html: str = ""


class LocalizedGameInfo(Model):
    locale: str
    steam_language: str
    store_country: str | None = None
    name: str | None = None
    short_description: TextValue | None = None
    full_description: TextValue | None = None


class MediaImage(Model):
    thumbnail_url: str | None = None
    full_url: str | None = None
    id: int | None = None


class MediaVideo(Model):
    id: int | None = None
    name: str | None = None
    thumbnail_url: str | None = None
    dash_av1_url: str | None = None
    dash_h264_url: str | None = None
    hls_h264_url: str | None = None
    highlight: bool | None = None


class Requirements(Model):
    minimum: TextValue | None = None
    recommended: TextValue | None = None


class RequirementsByOs(Model):
    windows: Requirements | None = None
    mac: Requirements | None = None
    linux: Requirements | None = None


class LanguageSupport(Model):
    name: str
    steam_language: str | None = None
    web_code: str | None = None
    interface: bool | None = None
    subtitles: bool | None = None
    full_audio: bool | None = None


class Category(Model):
    id: int | None = None
    name: str
    source: str = "steam"


class Tag(Model):
    name: str
    source: str
    rank: int | None = None
    votes: int | None = None


class AgeRating(Model):
    authority: str
    rating: str | None = None
    required_age: int | None = None
    descriptors: list[str] = Field(default_factory=list)
    banned: bool | None = None
    use_age_gate: bool | None = None


class RatingSummary(Model):
    locale: str | None = None
    review_language: str | None = None
    store_country: str | None = None
    score: int | None = None
    score_description: str | None = None
    total_positive: int = 0
    total_negative: int = 0
    total_reviews: int = 0
    positive_percent: float | None = None


class ReviewAuthor(Model):
    steam_id: str | None = None
    games_owned: int | None = None
    reviews_written: int | None = None
    playtime_forever_minutes: int | None = None
    playtime_last_two_weeks_minutes: int | None = None
    playtime_at_review_minutes: int | None = None
    last_played: datetime | None = None


class Review(Model):
    recommendation_id: str
    text: str
    positive: bool
    source_url: str | None = None
    language: str | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None
    votes_up: int = 0
    votes_funny: int = 0
    weighted_vote_score: float | None = None
    comment_count: int = 0
    steam_purchase: bool | None = None
    received_for_free: bool | None = None
    written_during_early_access: bool | None = None
    developer_response: str | None = None
    author: ReviewAuthor | None = None


class ReviewCollection(Model):
    positive: list[Review] = Field(default_factory=list)
    negative: list[Review] = Field(default_factory=list)
    pages_requested: int = 1
    positive_requested: int = 4
    negative_requested: int = 4


class Achievement(Model):
    api_name: str | None = None
    name: str
    description: str | None = None
    global_percent: float | None = None
    hidden: bool | None = None
    icon_url: str | None = None


class HltbData(Model):
    id: int | None = None
    name: str
    url: str | None = None
    main_story_hours: float | None = None
    main_extra_hours: float | None = None
    completionist_hours: float | None = None
    all_styles_hours: float | None = None
    raw: dict[str, Any] = Field(default_factory=dict)


class MetacriticData(Model):
    url: str | None = None
    critic_score: int | None = None
    user_score: float | None = None
    user_score_raw: str | None = None
    platform: str | None = None


class Game(Model):
    platform: str = "steam"
    app_id: int
    store_url: str
    store_country: str | None = None
    type: str | None = None
    localizations: dict[str, LocalizedGameInfo] = Field(default_factory=dict)
    developers: list[str] = Field(default_factory=list)
    publishers: list[str] = Field(default_factory=list)
    release_date: date | None = None
    release_date_raw: str | None = None
    coming_soon: bool | None = None
    screenshots: list[MediaImage] = Field(default_factory=list)
    videos: list[MediaVideo] = Field(default_factory=list)
    header_image: str | None = None
    website: str | None = None
    requirements: RequirementsByOs = Field(default_factory=RequirementsByOs)
    supported_languages: list[LanguageSupport] = Field(default_factory=list)
    platforms: dict[str, bool] = Field(default_factory=dict)
    categories: list[Category] = Field(default_factory=list)
    genres: list[str] = Field(default_factory=list)
    tags: list[Tag] = Field(default_factory=list)
    age_ratings: list[AgeRating] = Field(default_factory=list)
    achievements: list[Achievement] = Field(default_factory=list)
    achievements_url: str | None = None
    steam_rating: RatingSummary | None = None
    ratings_by_locale: dict[str, RatingSummary] = Field(default_factory=dict)
    reviews: ReviewCollection = Field(default_factory=ReviewCollection)
    metacritic: MetacriticData | None = None
    how_long_to_beat: HltbData | None = None
    diagnostics: list[Diagnostic] = Field(default_factory=list)
