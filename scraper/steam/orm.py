from __future__ import annotations

from datetime import date, datetime

from sqlalchemy import Boolean, Date, DateTime, Float, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from scraper.wikidata.orm import Base


class SteamApp(Base):
    __tablename__ = "steam_apps"

    app_id: Mapped[int] = mapped_column(Integer, primary_key=True)
    type: Mapped[str | None] = mapped_column(String(32), nullable=True)
    demo_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    dlc_for_app_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    optional_dlc: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    required_app_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    windows_build: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    linux_build: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    mac_build: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    vac_enabled: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    metacritic_name: Mapped[str | None] = mapped_column(Text, nullable=True)
    metacritic_score: Mapped[int | None] = mapped_column(Integer, nullable=True)
    metacritic_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    gamepad_preferred: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    controller_support: Mapped[str | None] = mapped_column(String(32), nullable=True)
    release_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    release_date_max: Mapped[date | None] = mapped_column(Date, nullable=True)
    release_date_raw: Mapped[str | None] = mapped_column(Text, nullable=True)
    release_status: Mapped[str | None] = mapped_column(String(32), nullable=True)
    coming_soon: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    external_account_notice: Mapped[str | None] = mapped_column(Text, nullable=True)
    drm_notice: Mapped[str | None] = mapped_column(Text, nullable=True)
    website: Mapped[str | None] = mapped_column(Text, nullable=True)
    price_currency: Mapped[str | None] = mapped_column(String(8), nullable=True)
    price_initial: Mapped[int | None] = mapped_column(Integer, nullable=True)
    price_final: Mapped[int | None] = mapped_column(Integer, nullable=True)
    price_discount_percent: Mapped[int | None] = mapped_column(Integer, nullable=True)


class SteamAppLocalization(Base):
    __tablename__ = "steam_app_localizations"

    app_id: Mapped[int] = mapped_column(
        ForeignKey("steam_apps.app_id", ondelete="CASCADE"), primary_key=True
    )
    language: Mapped[str] = mapped_column(String(16), primary_key=True)
    store_country: Mapped[str] = mapped_column(String(2), primary_key=True, default="")
    name: Mapped[str | None] = mapped_column(Text, nullable=True)
    short_description: Mapped[str | None] = mapped_column(Text, nullable=True)
    short_description_html: Mapped[str | None] = mapped_column(Text, nullable=True)
    about: Mapped[str | None] = mapped_column(Text, nullable=True)
    about_html: Mapped[str | None] = mapped_column(Text, nullable=True)
    long_description: Mapped[str | None] = mapped_column(Text, nullable=True)
    long_description_html: Mapped[str | None] = mapped_column(Text, nullable=True)
    legal_notice: Mapped[str | None] = mapped_column(Text, nullable=True)
    legal_notice_html: Mapped[str | None] = mapped_column(Text, nullable=True)


class SteamMedia(Base):
    __tablename__ = "steam_media"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    app_id: Mapped[int] = mapped_column(
        ForeignKey("steam_apps.app_id", ondelete="CASCADE"), index=True
    )
    media_type: Mapped[str] = mapped_column(String(64))
    url: Mapped[str] = mapped_column(Text)
    format: Mapped[str | None] = mapped_column(String(16), nullable=True)
    language: Mapped[str | None] = mapped_column(String(16), nullable=True)
    thumbnail_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    full_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    media_id: Mapped[int | None] = mapped_column(Integer, nullable=True)


class SteamEdition(Base):
    __tablename__ = "steam_editions"

    package_id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str | None] = mapped_column(Text, nullable=True)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)


class SteamAppEdition(Base):
    __tablename__ = "steam_app_editions"

    app_id: Mapped[int] = mapped_column(
        ForeignKey("steam_apps.app_id", ondelete="CASCADE"), primary_key=True
    )
    package_id: Mapped[int] = mapped_column(
        ForeignKey("steam_editions.package_id", ondelete="CASCADE"), primary_key=True
    )


class SteamEditionPrice(Base):
    __tablename__ = "steam_edition_prices"

    package_id: Mapped[int] = mapped_column(
        ForeignKey("steam_editions.package_id", ondelete="CASCADE"), primary_key=True
    )
    store_country: Mapped[str] = mapped_column(String(2), primary_key=True, default="")
    currency: Mapped[str | None] = mapped_column(String(8), nullable=True)
    initial: Mapped[int | None] = mapped_column(Integer, nullable=True)
    final: Mapped[int | None] = mapped_column(Integer, nullable=True)
    discount_percent: Mapped[int | None] = mapped_column(Integer, nullable=True)
    price_type: Mapped[str | None] = mapped_column(String(16), nullable=True)
    period: Mapped[str | None] = mapped_column(String(16), nullable=True)
    period_units: Mapped[int | None] = mapped_column(Integer, nullable=True)


class SteamBundle(Base):
    __tablename__ = "steam_bundles"

    bundle_id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str | None] = mapped_column(Text, nullable=True)
    discount_percent: Mapped[int | None] = mapped_column(Integer, nullable=True)
    must_purchase_as_set: Mapped[bool | None] = mapped_column(Boolean, nullable=True)


class SteamBundleEdition(Base):
    __tablename__ = "steam_bundle_editions"

    bundle_id: Mapped[int] = mapped_column(
        ForeignKey("steam_bundles.bundle_id", ondelete="CASCADE"), primary_key=True
    )
    package_id: Mapped[int] = mapped_column(
        ForeignKey("steam_editions.package_id", ondelete="CASCADE"), primary_key=True
    )


class SteamBundlePrice(Base):
    __tablename__ = "steam_bundle_prices"

    bundle_id: Mapped[int] = mapped_column(
        ForeignKey("steam_bundles.bundle_id", ondelete="CASCADE"), primary_key=True
    )
    store_country: Mapped[str] = mapped_column(String(2), primary_key=True, default="")
    currency: Mapped[str | None] = mapped_column(String(8), nullable=True)
    discount_percent: Mapped[int | None] = mapped_column(Integer, nullable=True)
    initial: Mapped[int | None] = mapped_column(Integer, nullable=True)
    final: Mapped[int | None] = mapped_column(Integer, nullable=True)


class SteamExternalLink(Base):
    __tablename__ = "steam_external_links"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    app_id: Mapped[int] = mapped_column(
        ForeignKey("steam_apps.app_id", ondelete="CASCADE"), index=True
    )
    link_type: Mapped[str] = mapped_column(String(64))
    url: Mapped[str | None] = mapped_column(Text, nullable=True)
    value: Mapped[str | None] = mapped_column(Text, nullable=True)
    source_scope: Mapped[str] = mapped_column(String(160), default="")


class SteamAgeRating(Base):
    __tablename__ = "steam_age_ratings"

    app_id: Mapped[int] = mapped_column(
        ForeignKey("steam_apps.app_id", ondelete="CASCADE"), primary_key=True
    )
    age_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    standard: Mapped[str] = mapped_column(String(64))
    rating: Mapped[str | None] = mapped_column(String(128), nullable=True)
    minimum_age: Mapped[int | None] = mapped_column(Integer, nullable=True)
    rating_generated: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    use_age_gate: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    banned: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    descriptor_raw: Mapped[str | None] = mapped_column(Text, nullable=True)


class SteamDescriptor(Base):
    __tablename__ = "steam_descriptors"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    app_id: Mapped[int] = mapped_column(
        ForeignKey("steam_apps.app_id", ondelete="CASCADE"), index=True
    )
    age_id: Mapped[str] = mapped_column(String(64))
    steam_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    name: Mapped[str] = mapped_column(Text)


class SteamSystemRequirement(Base):
    __tablename__ = "steam_system_requirements"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    app_id: Mapped[int] = mapped_column(
        ForeignKey("steam_apps.app_id", ondelete="CASCADE"), index=True
    )
    platform: Mapped[str] = mapped_column(String(16))
    level: Mapped[str] = mapped_column(String(16))
    html: Mapped[str] = mapped_column(Text)


class SteamFeature(Base):
    __tablename__ = "steam_features"

    app_id: Mapped[int] = mapped_column(
        ForeignKey("steam_apps.app_id", ondelete="CASCADE"), primary_key=True
    )
    category_id: Mapped[int | None] = mapped_column(Integer, primary_key=True, nullable=True)
    english_name: Mapped[str] = mapped_column(Text, primary_key=True)


class SteamAccessibilityFeature(Base):
    __tablename__ = "steam_accessibility_features"

    app_id: Mapped[int] = mapped_column(
        ForeignKey("steam_apps.app_id", ondelete="CASCADE"), primary_key=True
    )
    category_id: Mapped[int | None] = mapped_column(Integer, primary_key=True, nullable=True)
    english_name: Mapped[str] = mapped_column(Text, primary_key=True)


class SteamDeckSupport(Base):
    __tablename__ = "steam_deck_support"

    app_id: Mapped[int] = mapped_column(
        ForeignKey("steam_apps.app_id", ondelete="CASCADE"), primary_key=True
    )
    status: Mapped[str] = mapped_column(String(16))


class SteamEula(Base):
    __tablename__ = "steam_eulas"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    app_id: Mapped[int] = mapped_column(
        ForeignKey("steam_apps.app_id", ondelete="CASCADE"), index=True
    )
    eula_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    url: Mapped[str | None] = mapped_column(Text, nullable=True)
    version: Mapped[str | None] = mapped_column(String(64), nullable=True)


class SteamController(Base):
    __tablename__ = "steam_controllers"

    app_id: Mapped[int] = mapped_column(
        ForeignKey("steam_apps.app_id", ondelete="CASCADE"), primary_key=True
    )
    controller: Mapped[str] = mapped_column(String(128), primary_key=True)
    bluetooth: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    usb: Mapped[bool | None] = mapped_column(Boolean, nullable=True)


class SteamOrganizationCredit(Base):
    __tablename__ = "steam_organization_credits"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    app_id: Mapped[int] = mapped_column(
        ForeignKey("steam_apps.app_id", ondelete="CASCADE"), index=True
    )
    status: Mapped[str] = mapped_column(String(16))
    organization_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    organization_name: Mapped[str] = mapped_column(Text)


class SteamSupportedLanguage(Base):
    __tablename__ = "steam_supported_languages"

    app_id: Mapped[int] = mapped_column(
        ForeignKey("steam_apps.app_id", ondelete="CASCADE"), primary_key=True
    )
    language: Mapped[str] = mapped_column(String(32), primary_key=True)
    interface: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    full_audio: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    subtitles: Mapped[bool | None] = mapped_column(Boolean, nullable=True)


class SteamBuildBranch(Base):
    __tablename__ = "steam_build_branches"

    app_id: Mapped[int] = mapped_column(
        ForeignKey("steam_apps.app_id", ondelete="CASCADE"), primary_key=True
    )
    name: Mapped[str] = mapped_column(String(128), primary_key=True)
    updated_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    build_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    download_size: Mapped[int | None] = mapped_column(Integer, nullable=True)
    disk_size: Mapped[int | None] = mapped_column(Integer, nullable=True)


class SteamReviewLanguageStat(Base):
    __tablename__ = "steam_review_language_stats"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    app_id: Mapped[int] = mapped_column(
        ForeignKey("steam_apps.app_id", ondelete="CASCADE"), index=True
    )
    language: Mapped[str | None] = mapped_column(String(16), nullable=True)
    total_reviews: Mapped[int] = mapped_column(Integer, default=0)
    total_negative: Mapped[int] = mapped_column(Integer, default=0)
    total_positive: Mapped[int] = mapped_column(Integer, default=0)
    review_score: Mapped[int | None] = mapped_column(Integer, nullable=True)
    source_scope: Mapped[str] = mapped_column(String(160), default="")


class SteamReview(Base):
    __tablename__ = "steam_reviews"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    app_id: Mapped[int] = mapped_column(
        ForeignKey("steam_apps.app_id", ondelete="CASCADE"), index=True
    )
    review_type: Mapped[str] = mapped_column(String(16))
    recommendation_id: Mapped[str] = mapped_column(String(128), index=True)
    user_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    playtime_forever: Mapped[int | None] = mapped_column(Integer, nullable=True)
    playtime_last_two_weeks: Mapped[int | None] = mapped_column(Integer, nullable=True)
    playtime_at_review: Mapped[int | None] = mapped_column(Integer, nullable=True)
    deck_playtime_at_review: Mapped[int | None] = mapped_column(Integer, nullable=True)
    datetime_last_played: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    datetime_created: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    datetime_updated: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    datetime_dev_responded: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    votes_up: Mapped[int] = mapped_column(Integer, default=0)
    votes_funny: Mapped[int] = mapped_column(Integer, default=0)
    weighted_vote_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    comment_count: Mapped[int] = mapped_column(Integer, default=0)
    steam_purchase: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    received_for_free: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    written_during_early_access: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    primarily_steam_deck: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    voted_up: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    language: Mapped[str | None] = mapped_column(String(16), nullable=True)
    review_text: Mapped[str] = mapped_column(Text)
    developer_response: Mapped[str | None] = mapped_column(Text, nullable=True)


class SteamExternalReview(Base):
    __tablename__ = "steam_external_reviews"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    app_id: Mapped[int] = mapped_column(
        ForeignKey("steam_apps.app_id", ondelete="CASCADE"), index=True
    )
    organization: Mapped[str] = mapped_column(Text)
    rating: Mapped[str | None] = mapped_column(String(128), nullable=True)
    url: Mapped[str | None] = mapped_column(Text, nullable=True)
    quote: Mapped[str | None] = mapped_column(Text, nullable=True)


class SteamAchievement(Base):
    __tablename__ = "steam_achievements"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    app_id: Mapped[int] = mapped_column(
        ForeignKey("steam_apps.app_id", ondelete="CASCADE"), index=True
    )
    achievement_key: Mapped[str] = mapped_column(String(256))
    achievement_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    api_name: Mapped[str | None] = mapped_column(String(256), nullable=True)
    icon_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    global_percent: Mapped[float | None] = mapped_column(Float, nullable=True)
    hidden: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    language: Mapped[str] = mapped_column(String(16), default="")


class SteamAchievementLocalization(Base):
    __tablename__ = "steam_achievement_localizations"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    achievement_id: Mapped[int] = mapped_column(
        ForeignKey("steam_achievements.id", ondelete="CASCADE"), index=True
    )
    language: Mapped[str] = mapped_column(String(16))
    name: Mapped[str] = mapped_column(Text)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)


__all__ = [
    "SteamAccessibilityFeature",
    "SteamAchievement",
    "SteamAchievementLocalization",
    "SteamAgeRating",
    "SteamAppEdition",
    "SteamAppLocalization",
    "SteamApp",
    "SteamBuildBranch",
    "SteamBundleEdition",
    "SteamBundlePrice",
    "SteamBundle",
    "SteamController",
    "SteamDeckSupport",
    "SteamDescriptor",
    "SteamEditionPrice",
    "SteamEdition",
    "SteamEula",
    "SteamExternalLink",
    "SteamExternalReview",
    "SteamFeature",
    "SteamMedia",
    "SteamOrganizationCredit",
    "SteamReviewLanguageStat",
    "SteamReview",
    "SteamSupportedLanguage",
    "SteamSystemRequirement",
]
