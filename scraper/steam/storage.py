from __future__ import annotations

from typing import Any

from pydantic import BaseModel
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from scraper.models import (
    BuildBranch,
    LocalizedGameInfo,
    RatingSummary,
    Review,
    SystemRequirement,
)

from .orm import (
    SteamAccessibilityFeature,
    SteamAchievement,
    SteamAchievementLocalization,
    SteamAgeRating,
    SteamApp,
    SteamAppEdition,
    SteamAppLocalization,
    SteamBuildBranch,
    SteamBundle,
    SteamBundleEdition,
    SteamBundlePrice,
    SteamController,
    SteamDescriptor,
    SteamEdition,
    SteamEditionPrice,
    SteamEula,
    SteamExternalLink,
    SteamExternalReview,
    SteamFeature,
    SteamMedia,
    SteamOrganizationCredit,
    SteamReview,
    SteamReviewLanguageStat,
    SteamSupportedLanguage,
    SteamSystemRequirement,
)
from .orm import (
    SteamDeckSupport as SteamDeckSupportRow,
)


def _field(value: object, name: str, default: Any = None) -> Any:
    if isinstance(value, BaseModel):
        return getattr(value, name, default)
    if isinstance(value, dict):
        return value.get(name, default)
    return default


def _items(value: object) -> list[object]:
    if isinstance(value, (list, tuple)):
        return list(value)
    return []


def _text(value: object) -> str | None:
    if value is None:
        return None
    text = _field(value, "text", value)
    return str(text) if text is not None else None


def _html(value: object) -> str | None:
    if value is None:
        return None
    html = _field(value, "html", None)
    return str(html) if html is not None else None


def _country_from_scope(scope: str) -> str:
    parts = scope.split(":")
    if len(parts) <= 2 or parts[2] == "none":
        return ""
    return parts[2]


def _language_from_scope(scope: str) -> str:
    parts = scope.split(":")
    return parts[1] if len(parts) > 1 else ""


def _is_english_language(value: str) -> bool:
    normalized = value.casefold().replace("_", "-")
    return normalized == "english" or normalized == "en" or normalized.startswith("en-")


async def _ensure_app(session: AsyncSession, app_id: int) -> SteamApp:
    app = await session.get(SteamApp, app_id)
    if app is None:
        app = SteamApp(app_id=app_id)
        session.add(app)
        await session.flush()
    return app


async def persist_steam_scope(
    session: AsyncSession,
    app_id: int,
    scope: str,
    data: object,
) -> None:
    """Persist one typed Steam refresh result into TZ-shaped tables."""

    await _ensure_app(session, app_id)
    kind = scope.split(":", maxsplit=1)[0]
    if kind == "details":
        await _persist_details(session, app_id, scope, data)
    elif kind == "store":
        await _persist_store(session, app_id, scope, data)
    elif kind == "achievements":
        await _persist_achievements(session, app_id, scope, data)
    elif kind == "rating":
        await _persist_rating(session, app_id, scope, data)
    elif kind == "reviews":
        await _persist_reviews(session, app_id, scope, data)
    elif kind == "branches":
        await _persist_branches(session, app_id, data)


async def remove_steam_scope(
    session: AsyncSession,
    app_id: int,
    scope: str,
    data: object | None = None,
) -> None:
    """Remove the typed rows owned by a refresh scope before replacement."""

    kind = scope.split(":", maxsplit=1)[0]
    if kind == "details":
        language = _language_from_scope(scope)
        await session.execute(
            delete(SteamAppLocalization).where(
                SteamAppLocalization.app_id == app_id,
                SteamAppLocalization.language == language,
            )
        )
        await session.execute(
            delete(SteamMedia).where(
                SteamMedia.app_id == app_id,
                SteamMedia.language == language,
            )
        )
        # Locale scopes own only localized text/media and their links. Global
        # metadata is replaced by the English owner below, never by a random
        # locale or by a country refresh.
        writes_global = _is_english_language(language) or (
            isinstance(data, dict) and bool(data.get("global_authoritative"))
        )
        if writes_global:
            for model in (
                SteamAgeRating,
                SteamDescriptor,
                SteamSystemRequirement,
                SteamFeature,
                SteamAccessibilityFeature,
                SteamOrganizationCredit,
                SteamExternalReview,
            ):
                await session.execute(delete(model).where(model.app_id == app_id))
        await session.execute(
            delete(SteamExternalLink).where(
                SteamExternalLink.app_id == app_id,
                (
                    SteamExternalLink.source_scope.like("details:%")
                    if writes_global
                    else SteamExternalLink.source_scope == scope
                ),
            )
        )
    elif kind == "store":
        language = _language_from_scope(scope)
        if _is_english_language(language):
            await session.execute(
                delete(SteamSupportedLanguage).where(SteamSupportedLanguage.app_id == app_id)
            )
        await session.execute(
            delete(SteamExternalLink).where(
                SteamExternalLink.app_id == app_id,
                SteamExternalLink.source_scope == scope,
            )
        )
        if _is_english_language(language):
            await session.execute(
                delete(SteamAccessibilityFeature).where(SteamAccessibilityFeature.app_id == app_id)
            )
            await session.execute(delete(SteamEula).where(SteamEula.app_id == app_id))
            await session.execute(delete(SteamController).where(SteamController.app_id == app_id))
            await session.execute(
                delete(SteamExternalReview).where(SteamExternalReview.app_id == app_id)
            )
    elif kind == "achievements":
        language = _language_from_scope(scope)
        await session.execute(
            delete(SteamAchievementLocalization).where(
                SteamAchievementLocalization.language == language,
                SteamAchievementLocalization.achievement_id.in_(
                    select(SteamAchievement.id).where(SteamAchievement.app_id == app_id)
                ),
            )
        )
    elif kind == "rating":
        await session.execute(
            delete(SteamReviewLanguageStat).where(
                SteamReviewLanguageStat.app_id == app_id,
                SteamReviewLanguageStat.source_scope == scope,
            )
        )
    elif kind == "reviews":
        review_type = scope.split(":", maxsplit=2)[1]
        await session.execute(
            delete(SteamReview).where(
                SteamReview.app_id == app_id,
                SteamReview.review_type == review_type,
            )
        )
    elif kind == "branches":
        await session.execute(delete(SteamBuildBranch).where(SteamBuildBranch.app_id == app_id))


async def remove_all_steam_data(session: AsyncSession, app_id: int) -> None:
    for model in (
        SteamAppLocalization,
        SteamMedia,
        SteamAppEdition,
        SteamAgeRating,
        SteamDescriptor,
        SteamSystemRequirement,
        SteamFeature,
        SteamAccessibilityFeature,
        SteamDeckSupportRow,
        SteamEula,
        SteamController,
        SteamOrganizationCredit,
        SteamSupportedLanguage,
        SteamBuildBranch,
        SteamReviewLanguageStat,
        SteamReview,
        SteamExternalLink,
        SteamExternalReview,
    ):
        await session.execute(delete(model).where(model.app_id == app_id))
    achievement_ids = select(SteamAchievement.id).where(SteamAchievement.app_id == app_id)
    await session.execute(
        delete(SteamAchievementLocalization).where(
            SteamAchievementLocalization.achievement_id.in_(achievement_ids)
        )
    )
    await session.execute(delete(SteamAchievement).where(SteamAchievement.app_id == app_id))
    await session.execute(delete(SteamApp).where(SteamApp.app_id == app_id))


async def _persist_details(
    session: AsyncSession,
    app_id: int,
    scope: str,
    data: object,
) -> None:
    if not isinstance(data, dict):
        return
    app = await _ensure_app(session, app_id)
    localized = data.get("localized")
    language = _field(localized, "locale") or _language_from_scope(scope)
    country = _field(localized, "store_country") or _country_from_scope(scope)
    global_data = data.get("global_data")
    write_global = bool(data.get("global_authoritative", _is_english_language(language)))

    if write_global:
        source_data = global_data if isinstance(global_data, dict) else data
        platforms = source_data.get("platforms") or {}
        metacritic_score = source_data.get("metacritic_score")
        app.type = source_data.get("type")
        app.demo_id = source_data.get("demo_id")
        app.dlc_for_app_id = source_data.get("dlc_for_app_id")
        app.optional_dlc = source_data.get("optional_dlc")
        app.required_app_id = source_data.get("required_app_id")
        app.windows_build = platforms.get("windows")
        app.linux_build = platforms.get("linux")
        app.mac_build = platforms.get("mac")
        app.vac_enabled = source_data.get("vac_enabled")
        app.metacritic_name = source_data.get("metacritic_name")
        app.metacritic_score = metacritic_score
        app.metacritic_url = source_data.get("metacritic_url")
        app.gamepad_preferred = source_data.get("gamepad_preferred")
        app.controller_support = source_data.get("controller_support")
        app.release_date = source_data.get("release_date")
        app.release_date_max = source_data.get("release_date_max")
        app.release_date_raw = source_data.get("release_date_raw")
        app.release_status = source_data.get("release_status")
        app.coming_soon = source_data.get("coming_soon")
        app.external_account_notice = _text(source_data.get("external_account_notice"))
        app.drm_notice = _text(source_data.get("drm_notice"))
        app.website = source_data.get("website")

    await session.execute(
        delete(SteamAppLocalization).where(
            SteamAppLocalization.app_id == app_id,
            SteamAppLocalization.language == language,
        )
    )
    if isinstance(localized, LocalizedGameInfo) or isinstance(localized, dict):
        session.add(
            SteamAppLocalization(
                app_id=app_id,
                language=language,
                store_country=country,
                name=_field(localized, "name"),
                short_description=_text(_field(localized, "short_description")),
                short_description_html=_html(_field(localized, "short_description")),
                about=_text(_field(localized, "about_the_game", _field(localized, "about"))),
                about_html=_html(_field(localized, "about_the_game", _field(localized, "about"))),
                long_description=_text(
                    _field(localized, "full_description", _field(localized, "detailed_description"))
                ),
                long_description_html=_html(
                    _field(localized, "full_description", _field(localized, "detailed_description"))
                ),
                legal_notice=_text(_field(localized, "legal_notice")),
                legal_notice_html=_html(_field(localized, "legal_notice")),
            )
        )

    await session.execute(
        delete(SteamMedia).where(
            SteamMedia.app_id == app_id,
            SteamMedia.language == language,
        )
    )
    seen_media: set[tuple[str, str, str]] = set()
    for item in _items(data.get("media")):
        url = _field(item, "url") or _field(item, "full_url")
        if not url:
            continue
        media_type = str(_field(item, "type") or _field(item, "media_type") or "unknown")
        media_language = str(_field(item, "language") or language)
        media_key = (media_type, str(url), media_language)
        if media_key in seen_media:
            continue
        seen_media.add(media_key)
        session.add(
            SteamMedia(
                app_id=app_id,
                media_type=media_type,
                url=str(url),
                format=_field(item, "format"),
                language=media_language,
                thumbnail_url=_field(item, "thumbnail_url"),
                full_url=_field(item, "full_url"),
                media_id=_field(item, "id"),
            )
        )

    if not write_global:
        return

    # All following relations are global and therefore must be built from the
    # authoritative English payload, not the requested locale.
    data = source_data

    old_links = await session.scalars(
        select(SteamAppEdition.package_id).where(SteamAppEdition.app_id == app_id)
    )
    old_package_ids = list(old_links)
    country = _country_from_scope(scope)
    if old_package_ids:
        await session.execute(
            delete(SteamEditionPrice).where(
                SteamEditionPrice.price_region == country.upper(),
                SteamEditionPrice.package_id.in_(old_package_ids),
            )
        )
    package_ids: set[int] = set()
    edition_links: list[int] = []
    for item in _items(data.get("editions")):
        package_id = _field(item, "package_id")
        if package_id is None:
            continue
        package_id = int(package_id)
        package_ids.add(package_id)
        edition = await session.get(SteamEdition, package_id)
        if edition is None:
            edition = SteamEdition(package_id=package_id)
            session.add(edition)
        edition.name = _field(item, "name")
        edition.description = _field(item, "description")
        edition.package_kind = _field(item, "package_kind")
        edition_links.append(package_id)
    await session.flush()
    for package_id in dict.fromkeys(edition_links):
        exists = await session.get(SteamAppEdition, {"app_id": app_id, "package_id": package_id})
        if exists is None:
            session.add(SteamAppEdition(app_id=app_id, package_id=package_id))
    for item in _items(data.get("edition_prices")):
        package_id = _field(item, "package_id")
        if package_id is None:
            continue
        session.add(
            SteamEditionPrice(
                package_id=int(package_id),
                price_region=str(_field(item, "price_region") or country.upper()),
                store_country=str(_field(item, "store_country") or country.upper()) or None,
                currency=_field(item, "currency"),
                initial=_field(item, "initial"),
                final=_field(item, "final"),
                discount_percent=_field(item, "discount_percent"),
                price_type=_field(item, "price_type"),
                period=_field(item, "period"),
                period_units=_field(item, "period_units"),
            )
        )
    await session.flush()

    for item in _items(data.get("bundles")):
        bundle_id = _field(item, "bundle_id")
        if bundle_id is None:
            continue
        bundle_id = int(bundle_id)
        bundle = await session.get(SteamBundle, bundle_id)
        if bundle is None:
            bundle = SteamBundle(bundle_id=bundle_id)
            session.add(bundle)
        bundle.name = _field(item, "name")
        bundle.discount_percent = _field(item, "discount_percent")
        bundle.must_purchase_as_set = _field(item, "must_purchase_as_set")
        for package_id in _field(item, "edition_package_ids", []) or []:
            package_id = int(package_id)
            included_edition = await session.get(SteamEdition, package_id)
            if included_edition is None:
                session.add(SteamEdition(package_id=package_id))
                await session.flush()
            link = await session.get(
                SteamBundleEdition,
                {"bundle_id": bundle_id, "package_id": package_id},
            )
            if link is None:
                session.add(SteamBundleEdition(bundle_id=bundle_id, package_id=package_id))
    bundle_price_ids = [
        int(_field(item, "bundle_id"))
        for item in _items(data.get("bundle_prices"))
        if _field(item, "bundle_id") is not None
    ]
    if bundle_price_ids:
        await session.execute(
            delete(SteamBundlePrice).where(
                SteamBundlePrice.price_region == country.upper(),
                SteamBundlePrice.bundle_id.in_(bundle_price_ids),
            )
        )
    for item in _items(data.get("bundle_prices")):
        bundle_id = _field(item, "bundle_id")
        if bundle_id is None:
            continue
        session.add(
            SteamBundlePrice(
                bundle_id=int(bundle_id),
                price_region=str(_field(item, "price_region") or country.upper()),
                store_country=str(_field(item, "store_country") or country.upper()) or None,
                currency=_field(item, "currency"),
                discount_percent=_field(item, "discount_percent"),
                initial=_field(item, "initial"),
                final=_field(item, "final"),
            )
        )

    for item in _items(data.get("age_ratings")):
        age_id = str(_field(item, "age_id") or _field(item, "authority"))
        session.add(
            SteamAgeRating(
                app_id=app_id,
                age_id=age_id,
                standard=str(_field(item, "authority") or age_id),
                rating=_field(item, "rating"),
                minimum_age=_field(item, "required_age"),
                rating_generated=_field(item, "rating_generated"),
                use_age_gate=_field(item, "use_age_gate"),
                banned=_field(item, "banned"),
                descriptor_raw=_field(item, "raw"),
            )
        )
    for item in _items(data.get("descriptors")):
        session.add(
            SteamDescriptor(
                app_id=app_id,
                age_id=str(_field(item, "age_id") or "steam"),
                steam_id=_field(item, "steam_id"),
                name=str(_field(item, "name") or ""),
            )
        )
    if _items(data.get("descriptors")) and not any(
        str(_field(item, "age_id") or "") == "steam" for item in _items(data.get("age_ratings"))
    ):
        session.add(
            SteamAgeRating(
                app_id=app_id,
                age_id="steam",
                standard="steam",
                descriptor_raw="content_descriptors",
            )
        )
    for item in _items(data.get("system_requirements")):
        if not isinstance(item, SystemRequirement) and not isinstance(item, dict):
            continue
        session.add(
            SteamSystemRequirement(
                app_id=app_id,
                platform=str(_field(item, "platform")),
                level=str(_field(item, "level")),
                html=str(_field(item, "html") or ""),
            )
        )
    for item in _items(data.get("categories")):
        category_id = _field(item, "id")
        if category_id is not None and 64 <= int(category_id) <= 79:
            continue
        session.add(
            SteamFeature(
                app_id=app_id,
                category_id=category_id,
                english_name=str(_field(item, "name") or _field(item, "description") or ""),
            )
        )
    for item in _items(data.get("accessibility_features")):
        session.add(
            SteamAccessibilityFeature(
                app_id=app_id,
                category_id=_field(item, "id"),
                english_name=str(_field(item, "name") or ""),
            )
        )
    for item in _items(data.get("organizations")):
        session.add(
            SteamOrganizationCredit(
                app_id=app_id,
                status=str(_field(item, "status") or ""),
                organization_id=_field(item, "id"),
                organization_name=str(_field(item, "name") or ""),
            )
        )
    for item in _items(data.get("external_links")):
        if _field(item, "url") is None and _field(item, "value") is None:
            continue
        session.add(
            SteamExternalLink(
                app_id=app_id,
                link_type=str(_field(item, "type") or "external"),
                url=_field(item, "url"),
                value=_field(item, "value"),
                source_scope=scope,
            )
        )
    for item in _items(data.get("external_reviews")):
        session.add(
            SteamExternalReview(
                app_id=app_id,
                organization=str(_field(item, "organization") or ""),
                rating=_field(item, "rating"),
                url=_field(item, "url"),
                quote=_field(item, "quote"),
            )
        )


async def _persist_store(
    session: AsyncSession,
    app_id: int,
    scope: str,
    data: object,
) -> None:
    if not isinstance(data, dict):
        return
    language_scope = _language_from_scope(scope)
    if not _is_english_language(language_scope):
        return
    if _is_english_language(language_scope):
        await session.execute(
            delete(SteamSupportedLanguage).where(SteamSupportedLanguage.app_id == app_id)
        )
    seen_languages: set[str] = set()
    for item in _items(data.get("supported_languages")):
        language = _field(item, "web_code") or _field(item, "name")
        if not language or language in seen_languages:
            continue
        seen_languages.add(str(language))
        session.add(
            SteamSupportedLanguage(
                app_id=app_id,
                language=str(language),
                interface=_field(item, "interface"),
                full_audio=_field(item, "full_audio"),
                subtitles=_field(item, "subtitles"),
            )
        )
    for item in _items(data.get("external_links")):
        if _field(item, "url") is None and _field(item, "value") is None:
            continue
        session.add(
            SteamExternalLink(
                app_id=app_id,
                link_type=str(_field(item, "type") or "external"),
                url=_field(item, "url"),
                value=_field(item, "value"),
                source_scope=scope,
            )
        )
    for item in _items(data.get("accessibility_features")):
        session.add(
            SteamAccessibilityFeature(
                app_id=app_id,
                category_id=_field(item, "id"),
                english_name=str(_field(item, "name") or ""),
            )
        )
    deck = data.get("deck_support")
    if deck is not None:
        deck_row = await session.get(SteamDeckSupportRow, app_id)
        if deck_row is None:
            deck_row = SteamDeckSupportRow(app_id=app_id, status="unknown")
            session.add(deck_row)
        deck_row.status = str(_field(deck, "status") or "unknown")
    for item in _items(data.get("eulas")):
        session.add(
            SteamEula(
                app_id=app_id,
                eula_id=_field(item, "id"),
                url=_field(item, "url"),
                version=_field(item, "version"),
            )
        )
    for item in _items(data.get("controllers")):
        name = _field(item, "name")
        if not name:
            continue
        session.add(
            SteamController(
                app_id=app_id,
                controller=str(name),
                bluetooth=_field(item, "bluetooth"),
                usb=_field(item, "usb"),
            )
        )
    for item in _items(data.get("external_reviews")):
        organization = _field(item, "organization")
        if not organization:
            continue
        session.add(
            SteamExternalReview(
                app_id=app_id,
                organization=str(organization),
                rating=_field(item, "rating"),
                url=_field(item, "url"),
                quote=_field(item, "quote"),
            )
        )


async def _persist_achievements(
    session: AsyncSession,
    app_id: int,
    scope: str,
    data: object,
) -> None:
    values = data.get("achievements") if isinstance(data, dict) else data
    language = _language_from_scope(scope)
    seen: set[str] = set()
    for item in _items(values):
        key = _field(item, "api_name")
        achievement_id = _field(item, "steam_id")
        name = _field(item, "name")
        if not key:
            key = str(achievement_id) if achievement_id is not None else str(name or "")
        if not key or key in seen:
            continue
        seen.add(str(key))
        achievement = await session.scalar(
            select(SteamAchievement).where(
                SteamAchievement.app_id == app_id,
                SteamAchievement.achievement_key == str(key),
            )
        )
        if achievement is None:
            achievement = SteamAchievement(app_id=app_id, achievement_key=str(key))
            session.add(achievement)
        achievement.achievement_id = achievement_id
        achievement.api_name = _field(item, "api_name")
        achievement.icon_url = _field(item, "icon_url")
        achievement.global_percent = _field(item, "global_percent")
        achievement.hidden = _field(item, "hidden")
        await session.flush()
        localization = await session.scalar(
            select(SteamAchievementLocalization).where(
                SteamAchievementLocalization.achievement_id == achievement.id,
                SteamAchievementLocalization.language == language,
            )
        )
        if localization is None:
            localization = SteamAchievementLocalization(
                achievement_id=achievement.id,
                language=language,
                name=str(name or ""),
            )
            session.add(localization)
        localization.name = str(name or "")
        localization.description = _field(item, "description")


async def _persist_rating(
    session: AsyncSession,
    app_id: int,
    scope: str,
    data: object,
) -> None:
    if not isinstance(data, RatingSummary) and not isinstance(data, dict):
        return
    session.add(
        SteamReviewLanguageStat(
            app_id=app_id,
            language=_field(data, "review_language"),
            total_reviews=int(_field(data, "total_reviews", 0) or 0),
            total_negative=int(_field(data, "total_negative", 0) or 0),
            total_positive=int(_field(data, "total_positive", 0) or 0),
            review_score=_field(data, "score"),
            source_scope=scope,
        )
    )


async def _persist_reviews(
    session: AsyncSession,
    app_id: int,
    scope: str,
    data: object,
) -> None:
    review_type = scope.split(":", maxsplit=2)[1]
    for item in _items(data):
        if not isinstance(item, Review) and not isinstance(item, dict):
            continue
        author = _field(item, "author")
        session.add(
            SteamReview(
                app_id=app_id,
                review_type=review_type,
                recommendation_id=str(_field(item, "recommendation_id") or ""),
                user_id=_field(author, "steam_id"),
                playtime_forever=_field(author, "playtime_forever_minutes"),
                playtime_last_two_weeks=_field(author, "playtime_last_two_weeks_minutes"),
                playtime_at_review=_field(author, "playtime_at_review_minutes"),
                deck_playtime_at_review=_field(item, "deck_playtime_at_review_minutes")
                or _field(author, "deck_playtime_at_review_minutes"),
                datetime_last_played=_field(author, "last_played"),
                datetime_created=_field(item, "created_at"),
                datetime_updated=_field(item, "updated_at"),
                datetime_dev_responded=_field(item, "developer_responded_at"),
                votes_up=int(_field(item, "votes_up", 0) or 0),
                votes_funny=int(_field(item, "votes_funny", 0) or 0),
                weighted_vote_score=_field(item, "weighted_vote_score"),
                comment_count=int(_field(item, "comment_count", 0) or 0),
                steam_purchase=_field(item, "steam_purchase"),
                received_for_free=_field(item, "received_for_free"),
                written_during_early_access=_field(item, "written_during_early_access"),
                primarily_steam_deck=_field(item, "primarily_steam_deck"),
                voted_up=_field(item, "voted_up", _field(item, "positive")),
                language=_field(item, "language"),
                review_text=str(_field(item, "text") or ""),
                developer_response=_field(item, "developer_response"),
            )
        )


async def _persist_branches(
    session: AsyncSession,
    app_id: int,
    data: object,
) -> None:
    values = data.get("branches") if isinstance(data, dict) else data
    for item in _items(values):
        if not isinstance(item, BuildBranch) and not isinstance(item, dict):
            continue
        name = _field(item, "name")
        if not name:
            continue
        session.add(
            SteamBuildBranch(
                app_id=app_id,
                name=str(name),
                updated_at=_field(item, "updated_at"),
                description=_field(item, "description"),
                build_id=_field(item, "build_id"),
                download_size=_field(item, "download_size"),
                disk_size=_field(item, "disk_size"),
            )
        )


__all__ = ["persist_steam_scope", "remove_all_steam_data", "remove_steam_scope"]
