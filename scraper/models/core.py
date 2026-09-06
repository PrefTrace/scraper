from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


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
    about: TextValue | None = None
    about_the_game: TextValue | None = None
    detailed_description: TextValue | None = None
    full_description: TextValue | None = None
    legal_notice: TextValue | None = None


class MediaImage(Model):
    thumbnail_url: str | None = None
    full_url: str | None = None
    id: int | None = None
    media_type: str = "screenshot"
    type: str | None = None
    url: str | None = None
    format: str | None = None
    language: str | None = None


class MediaVideo(Model):
    id: int | None = None
    name: str | None = None
    thumbnail_url: str | None = None
    dash_av1_url: str | None = None
    dash_h264_url: str | None = None
    hls_h264_url: str | None = None
    highlight: bool | None = None
    media_type: str = "trailer"
    type: str | None = None
    format: str | None = None
    language: str | None = None
    url: str | None = None


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


class AppRelationship(Model):
    """Relationships advertised by Steam for one application."""

    app_id: int | None = None
    demo_id: int | None = None
    dlc_for_app_id: int | None = None
    optional_dlc: bool | None = None
    required_app_id: int | None = None


class EditionInfo(Model):
    package_id: int
    name: str | None = None
    description: str | None = None
    package_kind: str | None = None


class EditionPrice(Model):
    package_id: int
    currency: str | None = None
    initial: int | None = None
    final: int | None = None
    discount_percent: int | None = None
    price_type: str = "one_time"
    period: str | None = None
    period_units: int | None = None
    price_region: str | None = None
    store_country: str | None = None


class Bundle(Model):
    bundle_id: int
    name: str | None = None
    discount_percent: int | None = None
    must_purchase_as_set: bool | None = None
    edition_package_ids: list[int] = Field(default_factory=list)


class BundlePrice(Model):
    bundle_id: int
    currency: str | None = None
    discount_percent: int | None = None
    initial: int | None = None
    final: int | None = None
    price_region: str | None = None
    store_country: str | None = None


class ExternalLink(Model):
    type: str
    url: str | None = None
    value: str | None = None


class Descriptor(Model):
    age_id: str
    steam_id: int | None = None
    name: str


class SystemRequirement(Model):
    platform: str
    level: str
    html: str


class Feature(Model):
    id: int | None = None
    name: str


class SteamDeckSupport(Model):
    status: str = "unknown"


class ThirdPartyEula(Model):
    id: int | str | None = None
    url: str | None = None
    version: str | None = None


class Controller(Model):
    name: str
    bluetooth: bool | None = None
    usb: bool | None = None


class OrganizationCredit(Model):
    name: str
    status: str
    id: int | None = None


class BuildBranch(Model):
    name: str
    updated_at: datetime | None = None
    description: str | None = None
    build_id: int | None = None
    download_size: int | None = None
    disk_size: int | None = None


class ReviewLanguageStats(Model):
    language: str | None = None
    total_reviews: int = 0
    total_negative: int = 0
    total_positive: int = 0
    review_score: int | None = None


class ExternalReview(Model):
    organization: str
    rating: str | None = None
    url: str | None = None
    quote: str | None = None


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
    rating_generated: bool | None = None
    raw: str | None = None
    age_id: str | None = None


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
    deck_playtime_at_review_minutes: int | None = None
    deck_playtime_at_review: int | None = None
    last_played: datetime | None = None


class Review(Model):
    recommendation_id: str
    text: str
    positive: bool
    voted_up: bool | None = None
    source_url: str | None = None
    language: str | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None
    developer_responded_at: datetime | None = None
    votes_up: int = 0
    votes_funny: int = 0
    weighted_vote_score: float | None = None
    comment_count: int = 0
    steam_purchase: bool | None = None
    received_for_free: bool | None = None
    written_during_early_access: bool | None = None
    primarily_steam_deck: bool | None = None
    deck_playtime_at_review_minutes: int | None = None
    deck_playtime_at_review: int | None = None
    developer_response: str | None = None
    author: ReviewAuthor | None = None
    text_length_chars: int = 0
    word_count: int = 0
    alpha_ratio: float = 0.0
    quality_status: str = "accepted"
    quality_reason: str | None = None
    selection_rank: int | None = None


class ReviewCollection(Model):
    positive: list[Review] = Field(default_factory=list)
    negative: list[Review] = Field(default_factory=list)
    pages_requested: int | None = None
    positive_requested: int = 4
    negative_requested: int = 4


class Achievement(Model):
    api_name: str | None = None
    name: str
    description: str | None = None
    global_percent: float | None = None
    hidden: bool | None = None
    icon_url: str | None = None
    language: str | None = None
    steam_id: int | None = None


class HltbData(Model):
    id: int | None = None
    name: str
    url: str | None = None
    main_story_hours: float | None = None
    main_extra_hours: float | None = None
    completionist_hours: float | None = None
    all_styles_hours: float | None = None


class MetacriticData(Model):
    url: str | None = None
    critic_score: int | None = None
    user_score: float | None = None
    user_score_raw: str | None = None
    platform: str | None = None


class PriceOverview(Model):
    """Steam price for the requested store country; amounts are in minor units."""

    currency: str
    initial: int | None = None
    final: int | None = None
    discount_percent: int | None = None
    initial_formatted: str | None = None
    final_formatted: str | None = None
