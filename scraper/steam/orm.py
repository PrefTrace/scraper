from __future__ import annotations

from datetime import date, datetime

from sqlalchemy import (
    Boolean,
    Date,
    DateTime,
    Float,
    ForeignKey,
    ForeignKeyConstraint,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, synonym

from scraper.wikidata_deprecated.orm import Base


class SteamApp(Base):
    __tablename__ = "steam_apps"

    app_id: Mapped[int] = mapped_column(Integer, primary_key=True)
    type: Mapped[str | None] = mapped_column(String(32), nullable=True)
    demo_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    dlc_for_app_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    optional_dlc: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    required_app_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    linux_build: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    windows_build: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    mac_build: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    vac_enabled: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    required_age: Mapped[int | None] = mapped_column(Integer, nullable=True)
    metacritic_score: Mapped[int | None] = mapped_column(Integer, nullable=True)
    metacritic_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    gamepad_preferred: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    controller_support: Mapped[str | None] = mapped_column(String(32), nullable=True)
    release_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    release_date_max: Mapped[date | None] = mapped_column(Date, nullable=True)
    release_status: Mapped[str | None] = mapped_column(String(32), nullable=True)
    external_account_notice: Mapped[str | None] = mapped_column(Text, nullable=True)
    drm_notice: Mapped[str | None] = mapped_column(Text, nullable=True)


class SteamAppLocalization(Base):
    __tablename__ = "steam_app_localizations"

    app_id: Mapped[int] = mapped_column(
        ForeignKey("steam_apps.app_id", ondelete="CASCADE"), primary_key=True
    )
    language: Mapped[str] = mapped_column(String(16), primary_key=True)
    name: Mapped[str | None] = mapped_column(Text, nullable=True)
    short_description: Mapped[str | None] = mapped_column(Text, nullable=True)
    about: Mapped[str | None] = mapped_column(Text, nullable=True)
    long_description: Mapped[str | None] = mapped_column(Text, nullable=True)
    legal_notice: Mapped[str | None] = mapped_column(Text, nullable=True)


class SteamMedia(Base):
    __tablename__ = "steam_media"
    __table_args__ = (
        UniqueConstraint("app_id", "media_type", "url", "language", name="uq_steam_media"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    app_id: Mapped[int] = mapped_column(
        ForeignKey("steam_apps.app_id", ondelete="CASCADE"), index=True
    )
    media_type: Mapped[str] = mapped_column(String(64))
    url: Mapped[str] = mapped_column(Text)
    format: Mapped[str | None] = mapped_column(String(16), nullable=True)
    language: Mapped[str | None] = mapped_column(String(16), nullable=True)


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
    price_region: Mapped[str] = mapped_column(String(32), primary_key=True)
    currency: Mapped[str | None] = mapped_column(String(16), nullable=True)
    initial: Mapped[int | None] = mapped_column(Integer, nullable=True)
    final: Mapped[int | None] = mapped_column(Integer, nullable=True)
    discount_percent: Mapped[int | None] = mapped_column(Integer, nullable=True)
    discount_description: Mapped[str | None] = mapped_column(Text, nullable=True)
    discount_end_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
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
    price_region: Mapped[str] = mapped_column(String(32), primary_key=True)
    currency: Mapped[str | None] = mapped_column(String(16), nullable=True)
    effective_discount_percent: Mapped[int | None] = mapped_column(Integer, nullable=True)
    initial: Mapped[int | None] = mapped_column(Integer, nullable=True)
    final: Mapped[int | None] = mapped_column(Integer, nullable=True)
    discount_description: Mapped[str | None] = mapped_column(Text, nullable=True)
    discount_end_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


class SteamExternalLink(Base):
    __tablename__ = "steam_external_links"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    app_id: Mapped[int] = mapped_column(
        ForeignKey("steam_apps.app_id", ondelete="CASCADE"), index=True
    )
    link_type: Mapped[str] = mapped_column("type", String(64))
    url: Mapped[str | None] = mapped_column(Text, nullable=True)
    value: Mapped[str | None] = mapped_column(Text, nullable=True)


class SteamAgeRating(Base):
    __tablename__ = "steam_age_ratings"

    age_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    app_id: Mapped[int] = mapped_column(
        ForeignKey("steam_apps.app_id", ondelete="CASCADE"), index=True
    )
    standard: Mapped[str] = mapped_column(String(64))
    rating_generated: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    use_age_gate: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    banned: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    rating: Mapped[str | None] = mapped_column(String(128), nullable=True)
    minimum_age: Mapped[int | None] = mapped_column(Integer, nullable=True)
    descriptor_raw: Mapped[str | None] = mapped_column(Text, nullable=True)


class SteamDescriptor(Base):
    __tablename__ = "steam_descriptors"
    __table_args__ = (UniqueConstraint("age_id", "steam_id", "name", name="uq_steam_descriptor"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    age_id: Mapped[str] = mapped_column(
        ForeignKey("steam_age_ratings.age_id", ondelete="CASCADE"), index=True
    )
    steam_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    name: Mapped[str | None] = mapped_column(Text, nullable=True)


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
    category_id: Mapped[int] = mapped_column(Integer, primary_key=True, nullable=False)
    english_name: Mapped[str | None] = mapped_column(Text, nullable=True)


class SteamAccessibilityFeature(Base):
    __tablename__ = "steam_accessibility_features"

    app_id: Mapped[int] = mapped_column(
        ForeignKey("steam_apps.app_id", ondelete="CASCADE"), primary_key=True
    )
    category_id: Mapped[int] = mapped_column(Integer, primary_key=True, nullable=False)
    english_name: Mapped[str | None] = mapped_column(Text, nullable=True)


class SteamDeckSupport(Base):
    __tablename__ = "steam_deck_support"

    app_id: Mapped[int] = mapped_column(
        ForeignKey("steam_apps.app_id", ondelete="CASCADE"), primary_key=True
    )
    status: Mapped[str] = mapped_column(String(16))


class SteamEula(Base):
    __tablename__ = "steam_eulas"
    __table_args__ = (UniqueConstraint("app_id", "eula_id", name="uq_steam_eula_source"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    app_id: Mapped[int] = mapped_column(
        ForeignKey("steam_apps.app_id", ondelete="CASCADE"), index=True
    )
    eula_id: Mapped[str | None] = mapped_column(String(256), nullable=True)
    name_description: Mapped[str | None] = mapped_column(Text, nullable=True)
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
    __table_args__ = (
        UniqueConstraint(
            "app_id",
            "status",
            "creator_clan_account_id",
            "credited_name",
            name="uq_steam_org_credit",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    app_id: Mapped[int] = mapped_column(
        ForeignKey("steam_apps.app_id", ondelete="CASCADE"), index=True
    )
    status: Mapped[str] = mapped_column(String(16))
    creator_clan_account_id: Mapped[int | None] = mapped_column(
        ForeignKey("steam_organizations.creator_clan_account_id", ondelete="SET NULL"),
        nullable=True,
    )
    credited_name: Mapped[str] = mapped_column(Text)
    organization_name = synonym("credited_name")


class SteamOrganization(Base):
    __tablename__ = "steam_organizations"

    creator_clan_account_id: Mapped[int] = mapped_column(Integer, primary_key=True)
    slug: Mapped[str | None] = mapped_column(Text, nullable=True)
    name: Mapped[str | None] = mapped_column(Text, nullable=True)
    homepage: Mapped[str | None] = mapped_column(Text, nullable=True)
    follower_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    logo_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    background_url: Mapped[str | None] = mapped_column(Text, nullable=True)


class SteamSupportedLanguage(Base):
    __tablename__ = "steam_supported_languages"

    app_id: Mapped[int] = mapped_column(
        ForeignKey("steam_apps.app_id", ondelete="CASCADE"), primary_key=True
    )
    language: Mapped[str] = mapped_column(String(32), primary_key=True)
    audio: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    text: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    subtitles: Mapped[bool | None] = mapped_column(Boolean, nullable=True)


class SteamPackageCountryRestriction(Base):
    __tablename__ = "steam_package_country_restrictions"

    package_id: Mapped[int] = mapped_column(
        ForeignKey("steam_editions.package_id", ondelete="CASCADE"), primary_key=True
    )
    restriction_type: Mapped[str] = mapped_column(String(32), primary_key=True)
    country_code: Mapped[str] = mapped_column(String(2), primary_key=True)


class SteamDepot(Base):
    __tablename__ = "steam_depots"

    depot_id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str | None] = mapped_column(Text, nullable=True)
    language: Mapped[str | None] = mapped_column(String(32), nullable=True)
    architecture: Mapped[str | None] = mapped_column(String(16), nullable=True)
    low_violence: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    dlc_app_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    optional_dlc_app_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    depot_from_app: Mapped[int | None] = mapped_column(Integer, nullable=True)
    shared_install: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    system_defined: Mapped[bool | None] = mapped_column(Boolean, nullable=True)


class SteamAppDepot(Base):
    __tablename__ = "steam_app_depots"

    app_id: Mapped[int] = mapped_column(
        ForeignKey("steam_apps.app_id", ondelete="CASCADE"), primary_key=True
    )
    depot_id: Mapped[int] = mapped_column(
        ForeignKey("steam_depots.depot_id", ondelete="CASCADE"), primary_key=True
    )


class SteamDepotOs(Base):
    __tablename__ = "steam_depot_os"

    depot_id: Mapped[int] = mapped_column(
        ForeignKey("steam_depots.depot_id", ondelete="CASCADE"), primary_key=True
    )
    os: Mapped[str] = mapped_column(String(16), primary_key=True)


class SteamDepotManifest(Base):
    __tablename__ = "steam_depot_manifests"

    depot_id: Mapped[int] = mapped_column(
        ForeignKey("steam_depots.depot_id", ondelete="CASCADE"), primary_key=True
    )
    branch: Mapped[str] = mapped_column(String(128), primary_key=True)
    manifest_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    download_size: Mapped[int | None] = mapped_column(Integer, nullable=True)
    disk_size: Mapped[int | None] = mapped_column(Integer, nullable=True)


class SteamTag(Base):
    __tablename__ = "steam_tags"

    app_id: Mapped[int] = mapped_column(
        ForeignKey("steam_apps.app_id", ondelete="CASCADE"), primary_key=True
    )
    tag_id: Mapped[int] = mapped_column(Integer, primary_key=True)
    weight: Mapped[int | None] = mapped_column(Integer, nullable=True)


class SteamTagLocalization(Base):
    __tablename__ = "steam_tag_localizations"

    tag_id: Mapped[int] = mapped_column(Integer, primary_key=True)
    language: Mapped[str] = mapped_column(String(16), primary_key=True)
    name: Mapped[str] = mapped_column(Text)


class SteamGenre(Base):
    __tablename__ = "steam_genres"

    app_id: Mapped[int] = mapped_column(
        ForeignKey("steam_apps.app_id", ondelete="CASCADE"), primary_key=True
    )
    genre_id: Mapped[int] = mapped_column(Integer, primary_key=True)


class SteamGenreLocalization(Base):
    __tablename__ = "steam_genre_localizations"

    genre_id: Mapped[int] = mapped_column(Integer, primary_key=True)
    language: Mapped[str] = mapped_column(String(16), primary_key=True)
    name: Mapped[str] = mapped_column(Text)


class SteamWorkshopStats(Base):
    __tablename__ = "steam_workshop_stats"

    app_id: Mapped[int] = mapped_column(
        ForeignKey("steam_apps.app_id", ondelete="CASCADE"), primary_key=True
    )
    workshop_available: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    published_file_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    collection_count: Mapped[int | None] = mapped_column(Integer, nullable=True)


class SteamBuildBranch(Base):
    __tablename__ = "steam_build_branches"

    app_id: Mapped[int] = mapped_column(
        ForeignKey("steam_apps.app_id", ondelete="CASCADE"), primary_key=True
    )
    name: Mapped[str] = mapped_column(String(128), primary_key=True)
    updated_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    build_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    download_size_min: Mapped[int | None] = mapped_column(Integer, nullable=True)
    download_size_median: Mapped[int | None] = mapped_column(Integer, nullable=True)
    download_size_max: Mapped[int | None] = mapped_column(Integer, nullable=True)
    disk_size_min: Mapped[int | None] = mapped_column(Integer, nullable=True)
    disk_size_median: Mapped[int | None] = mapped_column(Integer, nullable=True)
    disk_size_max: Mapped[int | None] = mapped_column(Integer, nullable=True)


class SteamReviewLanguageStat(Base):
    __tablename__ = "steam_review_language_stats"
    __table_args__ = (UniqueConstraint("app_id", "language", name="uq_steam_review_language_stat"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    app_id: Mapped[int] = mapped_column(
        ForeignKey("steam_apps.app_id", ondelete="CASCADE"), index=True
    )
    language: Mapped[str] = mapped_column(String(16), nullable=False)
    total_reviews: Mapped[int] = mapped_column(Integer, default=0)
    total_negative: Mapped[int] = mapped_column(Integer, default=0)
    total_positive: Mapped[int] = mapped_column(Integer, default=0)
    review_score: Mapped[int | None] = mapped_column(Integer, nullable=True)


class SteamReview(Base):
    __tablename__ = "steam_reviews"
    __table_args__ = (
        UniqueConstraint("app_id", "recommendation_id", name="uq_steam_review_source"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    app_id: Mapped[int] = mapped_column(
        ForeignKey("steam_apps.app_id", ondelete="CASCADE"), index=True
    )
    # TZ extension: Steam's recommendationid is the stable source identity.
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
    __table_args__ = (ForeignKeyConstraint(["app_id"], ["steam_apps.app_id"], ondelete="CASCADE"),)

    app_id: Mapped[int] = mapped_column(Integer, primary_key=True)
    achievement_id: Mapped[str] = mapped_column(String(256), primary_key=True)
    icon_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    global_percent: Mapped[float | None] = mapped_column(Float, nullable=True)
    hidden: Mapped[bool | None] = mapped_column(Boolean, nullable=True)


class SteamAchievementLocalization(Base):
    __tablename__ = "steam_achievement_localizations"
    __table_args__ = (
        UniqueConstraint(
            "app_id", "achievement_id", "language", name="uq_steam_achievement_localization"
        ),
        ForeignKeyConstraint(
            ["app_id", "achievement_id"],
            ["steam_achievements.app_id", "steam_achievements.achievement_id"],
            ondelete="CASCADE",
        ),
    )

    app_id: Mapped[int] = mapped_column(Integer, primary_key=True)
    achievement_id: Mapped[str] = mapped_column(String(256), primary_key=True)
    language: Mapped[str] = mapped_column(String(16), primary_key=True)
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
    "SteamAppDepot",
    "SteamBuildBranch",
    "SteamBundleEdition",
    "SteamBundlePrice",
    "SteamBundle",
    "SteamController",
    "SteamDepot",
    "SteamDepotManifest",
    "SteamDepotOs",
    "SteamDeckSupport",
    "SteamDescriptor",
    "SteamEditionPrice",
    "SteamEdition",
    "SteamEula",
    "SteamExternalLink",
    "SteamExternalReview",
    "SteamFeature",
    "SteamGenre",
    "SteamGenreLocalization",
    "SteamMedia",
    "SteamOrganization",
    "SteamOrganizationCredit",
    "SteamPackageCountryRestriction",
    "SteamReviewLanguageStat",
    "SteamReview",
    "SteamSupportedLanguage",
    "SteamSystemRequirement",
    "SteamTag",
    "SteamTagLocalization",
    "SteamWorkshopStats",
]
