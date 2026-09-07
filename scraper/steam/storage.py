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

from .locales import normalize_steam_language
from .orm import (
    SteamAccessibilityFeature,
    SteamAchievement,
    SteamAchievementLocalization,
    SteamAgeRating,
    SteamApp,
    SteamAppDepot,
    SteamAppEdition,
    SteamAppLocalization,
    SteamBuildBranch,
    SteamBundle,
    SteamBundleEdition,
    SteamBundlePrice,
    SteamCategoryLocalization,
    SteamController,
    SteamDepot,
    SteamDepotManifest,
    SteamDepotOs,
    SteamDescriptor,
    SteamEdition,
    SteamEditionPrice,
    SteamEula,
    SteamExternalLink,
    SteamExternalReview,
    SteamFeature,
    SteamGenre,
    SteamGenreLocalization,
    SteamMedia,
    SteamOrganization,
    SteamOrganizationCredit,
    SteamReview,
    SteamReviewLanguageStat,
    SteamSupportedLanguage,
    SteamSystemRequirement,
    SteamTag,
    SteamTagLocalization,
    SteamWorkshopStats,
)
from .orm import SteamDeckSupport as SteamDeckSupportRow

_CANONICAL_MEDIA_TYPES = {
    "screenshot",
    "trailer",
    "header_capsule",
    "small_capsule",
    "main_capsule",
    "vertical_capsule",
    "page_background",
    "library_capsule",
    "library_header",
    "library_hero",
    "library_logo",
}


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
    if text is None:
        return None
    normalized = str(text).strip()
    return normalized or None


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


def _currency(value: object) -> str:
    """Read an authoritative ISO currency without deriving it from country."""

    currency = _field(value, "currency")
    return str(currency).strip().upper() if currency not in (None, "") else ""


def _price_region(value: object) -> str:
    region = _field(value, "price_region")
    return str(region).strip().upper() if region not in (None, "") else ""


def _age_id(app_id: int, value: object) -> str:
    """Make the TZ age identity globally unique: ``appid:standard``."""

    raw = str(value or "steam").strip()
    prefix = f"{app_id}:"
    return raw if raw.startswith(prefix) else f"{prefix}{raw}"


async def _ensure_app(session: AsyncSession, app_id: int) -> SteamApp:
    app = await session.get(SteamApp, app_id)
    if app is None:
        app = SteamApp(app_id=app_id)
        session.add(app)
        await session.flush()
    return app


async def _gc_orphan_depots(session: AsyncSession) -> None:
    """Remove depot rows no longer referenced by any app."""

    orphan_ids = list(
        await session.scalars(
            select(SteamDepot.depot_id).where(
                ~SteamDepot.depot_id.in_(select(SteamAppDepot.depot_id))
            )
        )
    )
    if not orphan_ids:
        return
    await session.execute(delete(SteamDepotOs).where(SteamDepotOs.depot_id.in_(orphan_ids)))
    await session.execute(
        delete(SteamDepotManifest).where(SteamDepotManifest.depot_id.in_(orphan_ids))
    )
    await session.execute(delete(SteamDepot).where(SteamDepot.depot_id.in_(orphan_ids)))


async def persist_steam_scope(
    session: AsyncSession,
    app_id: int,
    scope: str,
    data: object,
) -> None:
    """Persist one typed Steam refresh result into TZ-shaped tables."""

    kind = scope.split(":", maxsplit=1)[0]
    if kind == "details" and isinstance(data, dict):
        # Demos, videos and hardware are discovery noise for this TZ scope.
        # Remove a previously indexed row as well, but do not create one for
        # an unsupported detail response in the first place.
        source_type = data.get("source_type")
        if source_type not in (None, "") and data.get("type") is None:
            await remove_all_steam_data(session, app_id)
            return
    await _ensure_app(session, app_id)
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
        media_delete = delete(SteamMedia).where(SteamMedia.app_id == app_id)
        if not _is_english_language(language):
            media_delete = media_delete.where(SteamMedia.language == language)
        await session.execute(media_delete)
        # Locale scopes own only localized text/media and their links. Global
        # metadata is replaced by the English owner below, never by a random
        # locale or by a country refresh.
        writes_global = _is_english_language(language) or (
            isinstance(data, dict) and bool(data.get("global_authoritative"))
        )
        if writes_global:
            age_ids = select(SteamAgeRating.age_id).where(SteamAgeRating.app_id == app_id)
            await session.execute(
                delete(SteamDescriptor).where(SteamDescriptor.age_id.in_(age_ids))
            )
            for model in (
                SteamAgeRating,
                SteamSystemRequirement,
                SteamFeature,
                SteamAccessibilityFeature,
                SteamOrganizationCredit,
                SteamGenre,
            ):
                await session.execute(delete(model).where(model.app_id == app_id))
        if writes_global:
            await session.execute(
                delete(SteamExternalLink).where(SteamExternalLink.app_id == app_id)
            )
    elif kind == "store":
        language = _language_from_scope(scope)
        if _is_english_language(language):
            await session.execute(
                delete(SteamSupportedLanguage).where(SteamSupportedLanguage.app_id == app_id)
            )
        if _is_english_language(language):
            await session.execute(delete(SteamEula).where(SteamEula.app_id == app_id))
            await session.execute(delete(SteamController).where(SteamController.app_id == app_id))
            await session.execute(delete(SteamTag).where(SteamTag.app_id == app_id))
            await session.execute(
                delete(SteamWorkshopStats).where(SteamWorkshopStats.app_id == app_id)
            )
            await session.execute(
                delete(SteamExternalReview).where(SteamExternalReview.app_id == app_id)
            )
    elif kind == "achievements":
        language = _language_from_scope(scope)
        await session.execute(
            delete(SteamAchievementLocalization).where(
                SteamAchievementLocalization.app_id == app_id,
                SteamAchievementLocalization.language == language,
            )
        )
        if _is_english_language(language):
            await session.execute(delete(SteamAchievement).where(SteamAchievement.app_id == app_id))
    elif kind == "rating":
        language = scope.split(":", maxsplit=2)[1]
        await session.execute(
            delete(SteamReviewLanguageStat).where(
                SteamReviewLanguageStat.app_id == app_id,
                SteamReviewLanguageStat.language == ("*" if language == "all" else language),
            )
        )
    elif kind == "reviews":
        review_type = scope.split(":", maxsplit=2)[1]
        await session.execute(
            delete(SteamReview).where(
                SteamReview.app_id == app_id,
                SteamReview.voted_up.is_(review_type == "positive"),
            )
        )
    elif kind == "branches":
        await session.execute(delete(SteamBuildBranch).where(SteamBuildBranch.app_id == app_id))


async def remove_all_steam_data(session: AsyncSession, app_id: int) -> None:
    age_ids = select(SteamAgeRating.age_id).where(SteamAgeRating.app_id == app_id)
    await session.execute(delete(SteamDescriptor).where(SteamDescriptor.age_id.in_(age_ids)))
    await session.execute(delete(SteamAppEdition).where(SteamAppEdition.app_id == app_id))
    for model in (
        SteamAppLocalization,
        SteamMedia,
        SteamAgeRating,
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
        SteamTag,
        SteamGenre,
        SteamWorkshopStats,
        SteamAppDepot,
    ):
        await session.execute(delete(model).where(model.app_id == app_id))
    await session.execute(
        delete(SteamAchievementLocalization).where(SteamAchievementLocalization.app_id == app_id)
    )
    await session.execute(delete(SteamAchievement).where(SteamAchievement.app_id == app_id))
    await session.execute(delete(SteamAppDepot).where(SteamAppDepot.app_id == app_id))
    await session.execute(delete(SteamApp).where(SteamApp.app_id == app_id))
    orphan_packages = select(SteamEdition.package_id).where(
        ~SteamEdition.package_id.in_(select(SteamAppEdition.package_id)),
        ~SteamEdition.package_id.in_(select(SteamBundleEdition.package_id)),
    )
    await session.execute(
        delete(SteamEditionPrice).where(SteamEditionPrice.package_id.in_(orphan_packages))
    )
    await session.execute(delete(SteamEdition).where(SteamEdition.package_id.in_(orphan_packages)))
    orphan_bundles = select(SteamBundle.bundle_id).where(
        ~SteamBundle.bundle_id.in_(select(SteamBundleEdition.bundle_id))
    )
    await session.execute(
        delete(SteamBundlePrice).where(SteamBundlePrice.bundle_id.in_(orphan_bundles))
    )
    await session.execute(delete(SteamBundle).where(SteamBundle.bundle_id.in_(orphan_bundles)))
    await _gc_orphan_depots(session)


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
    global_data = data.get("global_data")
    write_global = bool(data.get("global_authoritative", _is_english_language(language)))

    localization_sources = [data]
    if isinstance(global_data, dict):
        localization_sources.append(global_data)
    category_localizations: dict[tuple[int, str], str] = {}
    genre_localizations: dict[tuple[int, str], str] = {}
    for localization_source in localization_sources:
        for item in _items(localization_source.get("category_localizations")):
            category_id = _field(item, "category_id", _field(item, "id"))
            item_language = _text(_field(item, "language"))
            name = _text(_field(item, "name", _field(item, "description")))
            if category_id is not None and item_language and name:
                category_localizations[(int(category_id), item_language)] = name
        for item in _items(localization_source.get("genre_localizations")):
            genre_id = _field(item, "genre_id", _field(item, "id"))
            item_language = _text(_field(item, "language"))
            name = _text(_field(item, "name", _field(item, "description")))
            if genre_id is not None and item_language and name:
                genre_localizations[(int(genre_id), item_language)] = name
    for (category_id, item_language), name in category_localizations.items():
        row = await session.get(SteamCategoryLocalization, (category_id, item_language))
        if row is None:
            row = SteamCategoryLocalization(
                category_id=category_id,
                language=item_language,
                name=name,
            )
            session.add(row)
        else:
            row.name = name
    for (genre_id, item_language), name in genre_localizations.items():
        row = await session.get(SteamGenreLocalization, (genre_id, item_language))
        if row is None:
            row = SteamGenreLocalization(
                genre_id=genre_id,
                language=item_language,
                name=name,
            )
            session.add(row)
        else:
            row.name = name

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
        app.metacritic_score = metacritic_score
        app.metacritic_url = source_data.get("metacritic_url")
        app.gamepad_preferred = source_data.get("gamepad_preferred")
        app.controller_support = source_data.get("controller_support_level") or "none"
        app.required_age = source_data.get("required_age")
        app.release_date = source_data.get("release_date")
        app.release_date_max = source_data.get("release_date_max")
        app.release_status = source_data.get("release_status")
        app.external_account_notice = _text(source_data.get("external_account_notice"))
        app.drm_notice = _text(source_data.get("drm_notice"))

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
                name=_field(localized, "name"),
                short_description=_text(_field(localized, "short_description")),
                about=_text(_field(localized, "about_the_game", _field(localized, "about"))),
                long_description=_text(
                    _field(localized, "full_description", _field(localized, "detailed_description"))
                ),
                legal_notice=_text(_field(localized, "legal_notice")),
            )
        )

    await session.execute(
        delete(SteamMedia).where(
            SteamMedia.app_id == app_id,
            SteamMedia.language == language,
        )
    )
    seen_media: set[tuple[str, str | None]] = set()
    for item in _items(data.get("media")):
        url = _field(item, "url") or _field(item, "full_url")
        if not url:
            continue
        media_type = str(_field(item, "type") or _field(item, "media_type") or "unknown")
        if media_type not in _CANONICAL_MEDIA_TYPES:
            continue
        raw_media_language = _field(item, "language")
        media_language = str(raw_media_language) if raw_media_language else None
        media_key = (str(url), media_language)
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
    if old_package_ids:
        await session.execute(delete(SteamAppEdition).where(SteamAppEdition.app_id == app_id))
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
        name = _text(_field(item, "name"))
        description = _text(_field(item, "description"))
        if name is not None:
            edition.name = name
        if description is not None:
            edition.description = description
        edition_links.append(package_id)
    await session.flush()
    for package_id in dict.fromkeys(edition_links):
        exists = await session.get(SteamAppEdition, {"app_id": app_id, "package_id": package_id})
        if exists is None:
            session.add(SteamAppEdition(app_id=app_id, package_id=package_id))
    for item in _items(data.get("edition_prices")):
        package_id = _field(item, "package_id")
        price_region = _price_region(item)
        currency = _currency(item)
        if package_id is None or not price_region:
            continue
        values = {
            "currency": currency or None,
            "initial": _field(item, "initial"),
            "final": _field(item, "final"),
            "discount_percent": _field(item, "discount_percent"),
            "discount_type": _text(_field(item, "discount_type")),
            "discount_end_at": _field(item, "discount_end_at"),
            "regional_edition": _field(item, "regional_edition"),
            "run_region_restricted": _field(item, "run_region_restricted"),
            "price_type": _field(item, "price_type"),
            "period": _field(item, "period"),
            "period_units": _field(item, "period_units"),
        }
        price_row = await session.get(SteamEditionPrice, (int(package_id), price_region))
        if price_row is None:
            session.add(
                SteamEditionPrice(package_id=int(package_id), price_region=price_region, **values)
            )
        else:
            for field_name, value in values.items():
                setattr(price_row, field_name, value)
    await session.flush()
    for item in _items(data.get("edition_metadata")):
        package_id = _field(item, "package_id")
        if package_id is None:
            continue
        edition = await session.get(SteamEdition, int(package_id))
        if edition is None:
            edition = SteamEdition(package_id=int(package_id))
            session.add(edition)
        name = _text(_field(item, "name"))
        description = _text(_field(item, "description"))
        if name is not None:
            edition.name = name
        if description is not None:
            edition.description = description
    await session.flush()

    current_bundle_ids = {
        int(_field(item, "bundle_id"))
        for item in _items(data.get("bundles"))
        if _field(item, "bundle_id") is not None
    }
    raw_bundle_status = data.get("bundle_membership_fetch_status")
    if isinstance(raw_bundle_status, dict) and raw_bundle_status:
        authoritative_bundle_ids = {
            bundle_id
            for bundle_id in current_bundle_ids
            if raw_bundle_status.get(bundle_id, raw_bundle_status.get(str(bundle_id))) is True
        }
    else:
        # Parser fixtures and legacy callers without a status map represent a
        # successful authoritative response.
        authoritative_bundle_ids = set(current_bundle_ids)
    raw_bundle_price_status = data.get("bundle_price_fetch_status")
    if isinstance(raw_bundle_price_status, dict) and raw_bundle_price_status:
        authoritative_bundle_price_ids = {
            bundle_id
            for bundle_id in current_bundle_ids
            if raw_bundle_price_status.get(
                bundle_id, raw_bundle_price_status.get(str(bundle_id))
            )
            is True
        }
    else:
        authoritative_bundle_price_ids = set(current_bundle_ids)
    if authoritative_bundle_ids:
        await session.execute(
            delete(SteamBundleEdition).where(
                SteamBundleEdition.bundle_id.in_(authoritative_bundle_ids)
            )
        )
        await session.execute(
            delete(SteamBundlePrice).where(
                SteamBundlePrice.bundle_id.in_(authoritative_bundle_ids)
            )
        )
    for item in _items(data.get("bundles")):
        bundle_id = _field(item, "bundle_id")
        if bundle_id is None:
            continue
        bundle_id = int(bundle_id)
        bundle = await session.get(SteamBundle, bundle_id)
        if bundle is None:
            bundle = SteamBundle(bundle_id=bundle_id)
            session.add(bundle)
        bundle.name = _text(_field(item, "name"))
        bundle.discount_percent = _field(item, "discount_percent")
        bundle.must_purchase_as_set = _field(item, "must_purchase_as_set")
        membership_is_authoritative = bundle_id in authoritative_bundle_ids
        for package_id in (
            _field(item, "edition_package_ids", []) or []
            if membership_is_authoritative
            else []
        ):
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
    for item in _items(data.get("bundle_prices")):
        bundle_id = _field(item, "bundle_id")
        price_region = _price_region(item)
        currency = _currency(item)
        if (
            bundle_id is None
            or int(bundle_id) not in authoritative_bundle_price_ids
            or not price_region
        ):
            continue
        values = {
            "currency": currency or None,
            "effective_discount_percent": _field(
                item, "effective_discount_percent", _field(item, "discount_percent")
            ),
            "initial": _field(item, "initial"),
            "final": _field(item, "final"),
            "discount_type": _text(_field(item, "discount_type")),
            "discount_end_at": _field(item, "discount_end_at"),
        }
        price_row = await session.get(SteamBundlePrice, (int(bundle_id), price_region))
        if price_row is None:
            session.add(
                SteamBundlePrice(bundle_id=int(bundle_id), price_region=price_region, **values)
            )
        else:
            for field_name, value in values.items():
                setattr(price_row, field_name, value)

    # A successful bundle response is also an authoritative observation of
    # its included package topology.  If Steam supplied no standalone sale
    # price for an included package in this observation region, retain the
    # explicit unavailable observation instead of silently dropping it.  Do
    # not overwrite a price discovered from a direct package offer.
    bundle_regions: dict[int, set[str]] = {}
    for item in _items(data.get("bundle_prices")):
        bundle_id = _field(item, "bundle_id")
        region = _price_region(item)
        if bundle_id is not None and region:
            bundle_regions.setdefault(int(bundle_id), set()).add(region)
    for item in _items(data.get("bundles")):
        bundle_id = _field(item, "bundle_id")
        if bundle_id is None or int(bundle_id) not in authoritative_bundle_ids:
            continue
        for package_id in _field(item, "edition_package_ids", []) or []:
            for region in bundle_regions.get(int(bundle_id), set()):
                if await session.get(SteamEditionPrice, (int(package_id), region)) is None:
                    session.add(
                        SteamEditionPrice(
                            package_id=int(package_id),
                            price_region=region,
                        )
                    )

    orphan_packages = select(SteamEdition.package_id).where(
        ~SteamEdition.package_id.in_(select(SteamAppEdition.package_id)),
        ~SteamEdition.package_id.in_(select(SteamBundleEdition.package_id)),
    )
    await session.execute(
        delete(SteamEditionPrice).where(SteamEditionPrice.package_id.in_(orphan_packages))
    )
    await session.execute(delete(SteamEdition).where(SteamEdition.package_id.in_(orphan_packages)))
    orphan_bundles = select(SteamBundle.bundle_id).where(
        ~SteamBundle.bundle_id.in_(select(SteamBundleEdition.bundle_id))
    )
    await session.execute(
        delete(SteamBundlePrice).where(SteamBundlePrice.bundle_id.in_(orphan_bundles))
    )
    await session.execute(delete(SteamBundle).where(SteamBundle.bundle_id.in_(orphan_bundles)))

    age_rating_ids: set[str] = set()
    for item in _items(data.get("age_ratings")):
        age_id = _age_id(app_id, _field(item, "age_id") or _field(item, "authority"))
        age_rating_ids.add(age_id)
        session.add(
            SteamAgeRating(
                age_id=age_id,
                app_id=app_id,
                standard=str(_field(item, "authority") or age_id),
                rating=_field(item, "rating"),
                minimum_age=_field(item, "minimum_age", _field(item, "required_age")),
                rating_generated=_field(item, "rating_generated"),
                use_age_gate=_field(item, "use_age_gate"),
                banned=_field(item, "banned"),
                descriptor_raw=_field(item, "raw"),
            )
        )
    for item in _items(data.get("descriptors")):
        from .parsers import normalize_descriptor_text

        descriptor_age_id = _age_id(app_id, _field(item, "age_id"))
        if descriptor_age_id not in age_rating_ids:
            age_rating_ids.add(descriptor_age_id)
            session.add(
                SteamAgeRating(
                    age_id=descriptor_age_id,
                    app_id=app_id,
                    standard=str(_field(item, "age_id") or "steam"),
                    descriptor_raw="content_descriptors",
                )
            )
        descriptor_name = _text(_field(item, "name"))
        title = _text(_field(source_data.get("localized"), "name"))
        descriptor_names = (
            normalize_descriptor_text(descriptor_name, title=title, language="en")
            if _field(item, "steam_id") is not None and descriptor_name
            else [descriptor_name]
        )
        for normalized_name in descriptor_names:
            session.add(
                SteamDescriptor(
                    age_id=descriptor_age_id,
                    steam_id=_field(item, "steam_id"),
                    name=normalized_name,
                )
            )
    for item in _items(data.get("system_requirements")):
        if not isinstance(item, SystemRequirement) and not isinstance(item, dict):
            continue
        platform = _text(_field(item, "platform"))
        level = _text(_field(item, "level"))
        html = _text(_field(item, "html"))
        if not platform or not level or not html:
            continue
        session.add(
            SteamSystemRequirement(
                app_id=app_id,
                platform=platform,
                level=level,
                html=html,
            )
        )
    for item in _items(data.get("categories")):
        category_id = _field(item, "id")
        if category_id is None or 64 <= int(category_id) <= 79:
            continue
        session.add(
            SteamFeature(
                app_id=app_id,
                category_id=category_id,
            )
        )
    for item in _items(data.get("accessibility_features")):
        category_id = _field(item, "id")
        if category_id is None:
            continue
        session.add(
            SteamAccessibilityFeature(
                app_id=app_id,
                category_id=category_id,
            )
        )
    for item in _items(data.get("organizations")):
        organization_name = _text(_field(item, "credited_name", _field(item, "name")))
        status = _text(_field(item, "status"))
        if not organization_name or not status:
            continue
        creator_id = _field(item, "creator_clan_account_id")
        if creator_id is not None and await session.get(SteamOrganization, int(creator_id)) is None:
            session.add(SteamOrganization(creator_clan_account_id=int(creator_id)))
        session.add(
            SteamOrganizationCredit(
                app_id=app_id,
                status=status,
                creator_clan_account_id=creator_id,
                credited_name=organization_name,
            )
        )
    for item in _items(data.get("organization_entities")):
        creator_id = _field(item, "creator_clan_account_id")
        if creator_id is None:
            continue
        organization = await session.get(SteamOrganization, int(creator_id))
        if organization is None:
            organization = SteamOrganization(creator_clan_account_id=int(creator_id))
            session.add(organization)
        for field_name in ("slug", "name", "homepage", "logo_url", "background_url"):
            value = _field(item, field_name)
            if value is not None:
                setattr(organization, field_name, _text(value))
        follower_count = _field(item, "follower_count")
        if follower_count is not None:
            organization.follower_count = int(follower_count)
    for item in _items(data.get("genre_rows")):
        genre_id = _field(item, "genre_id")
        if genre_id is None:
            continue
        session.add(SteamGenre(app_id=app_id, genre_id=int(genre_id)))
    for item in _items(data.get("external_links")):
        if _field(item, "url") is None and _field(item, "value") is None:
            continue
        session.add(
            SteamExternalLink(
                app_id=app_id,
                link_type=_text(_field(item, "type")) or "external",
                url=_text(_field(item, "url")),
                value=_text(_field(item, "value")),
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
    for item in _items(data.get("tag_localizations")):
        tag_id = _field(item, "tag_id")
        language = _text(_field(item, "language"))
        name = _text(_field(item, "name"))
        if tag_id is None or not language or not name:
            continue
        row = await session.get(SteamTagLocalization, (int(tag_id), language))
        if row is None:
            session.add(SteamTagLocalization(tag_id=int(tag_id), language=language, name=name))
        else:
            row.name = name
    for item in _items(data.get("structured_tags", data.get("tags"))):
        tag_id = _field(item, "tag_id")
        if tag_id is None:
            continue
        row = await session.get(SteamTag, (app_id, int(tag_id)))
        if row is None:
            row = SteamTag(app_id=app_id, tag_id=int(tag_id))
            session.add(row)
        row.weight = _field(item, "weight")
    if not _is_english_language(language_scope):
        return
    if _is_english_language(language_scope):
        await session.execute(
            delete(SteamSupportedLanguage).where(SteamSupportedLanguage.app_id == app_id)
        )
    seen_languages: set[str] = set()
    for item in _items(data.get("supported_languages")):
        language = _field(item, "web_code")
        if not language:
            raw_language = _field(item, "steam_language") or _field(item, "name")
            language = normalize_steam_language(str(raw_language)) if raw_language else None
        if not language or language in seen_languages:
            continue
        seen_languages.add(str(language))
        text_flag = _field(item, "text", _field(item, "interface"))
        audio_flag = _field(item, "audio", _field(item, "full_audio"))
        subtitles_flag = _field(item, "subtitles")
        session.add(
            SteamSupportedLanguage(
                app_id=app_id,
                language=str(language),
                text=None if text_flag is None else bool(text_flag),
                audio=None if audio_flag is None else bool(audio_flag),
                subtitles=None if subtitles_flag is None else bool(subtitles_flag),
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
                name_description=_text(_field(item, "name_description", _field(item, "name"))),
                url=_text(_field(item, "url")),
                version=_text(_field(item, "version")),
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
    for item in _items(data.get("organization_entities")):
        creator_id = _field(item, "creator_clan_account_id")
        if creator_id is None:
            continue
        organization = await session.get(SteamOrganization, int(creator_id))
        if organization is None:
            organization = SteamOrganization(creator_clan_account_id=int(creator_id))
            session.add(organization)
        for field_name in ("slug", "name", "homepage", "logo_url", "background_url"):
            value = _field(item, field_name)
            if value is not None:
                setattr(organization, field_name, _text(value))
        follower_count = _field(item, "follower_count")
        if follower_count is not None:
            organization.follower_count = int(follower_count)
    workshop = data.get("workshop_stats")
    if workshop is not None:
        row = await session.get(SteamWorkshopStats, app_id)
        if row is None:
            row = SteamWorkshopStats(app_id=app_id)
            session.add(row)
        row.workshop_available = _field(workshop, "workshop_available")
        row.published_file_count = _field(workshop, "published_file_count")
        row.collection_count = _field(workshop, "collection_count")
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
        key = _field(item, "achievement_id", _field(item, "api_name"))
        name = _field(item, "name")
        if not key or key in seen:
            continue
        seen.add(str(key))
        achievement = await session.get(SteamAchievement, (app_id, str(key)))
        if achievement is None:
            achievement = SteamAchievement(app_id=app_id, achievement_id=str(key))
            session.add(achievement)
        achievement.icon_url = _field(item, "icon_url")
        achievement.global_percent = _field(item, "global_percent")
        achievement.hidden = _field(item, "hidden")
        normalized_name = _text(name)
        if not normalized_name:
            continue
        localization = await session.get(SteamAchievementLocalization, (app_id, str(key), language))
        if localization is None:
            localization = SteamAchievementLocalization(
                app_id=app_id,
                achievement_id=str(key),
                language=language,
                name=normalized_name,
            )
            session.add(localization)
        localization.name = normalized_name
        localization.description = _field(item, "description")


async def _persist_rating(
    session: AsyncSession,
    app_id: int,
    scope: str,
    data: object,
) -> None:
    if not isinstance(data, RatingSummary) and not isinstance(data, dict):
        return
    language = _field(data, "review_language") or "*"
    await session.execute(
        delete(SteamReviewLanguageStat).where(
            SteamReviewLanguageStat.app_id == app_id,
            SteamReviewLanguageStat.language == language,
        )
    )
    session.add(
        SteamReviewLanguageStat(
            app_id=app_id,
            language=language,
            total_reviews=int(_field(data, "total_reviews", 0) or 0),
            total_negative=int(_field(data, "total_negative", 0) or 0),
            total_positive=int(_field(data, "total_positive", 0) or 0),
            review_score=_field(data, "score"),
        )
    )


async def _persist_reviews(
    session: AsyncSession,
    app_id: int,
    scope: str,
    data: object,
) -> None:
    for item in _items(data):
        if not isinstance(item, Review) and not isinstance(item, dict):
            continue
        author = _field(item, "author")
        recommendation_id = _text(_field(item, "recommendation_id"))
        review_text = _text(_field(item, "text"))
        if not recommendation_id or not review_text:
            continue
        session.add(
            SteamReview(
                app_id=app_id,
                recommendation_id=recommendation_id,
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
                review_text=review_text,
                developer_response=_field(item, "developer_response"),
            )
        )


async def _persist_branches(
    session: AsyncSession,
    app_id: int,
    data: object,
) -> None:
    await session.execute(delete(SteamBuildBranch).where(SteamBuildBranch.app_id == app_id))
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
                download_size_min=_field(item, "download_size_min"),
                download_size_median=_field(item, "download_size_median"),
                download_size_max=_field(item, "download_size_max"),
                disk_size_min=_field(item, "disk_size_min"),
                disk_size_median=_field(item, "disk_size_median"),
                disk_size_max=_field(item, "disk_size_max"),
            )
        )
    if not isinstance(data, dict):
        return

    # The branches scope owns the app -> depot set.  Replace it only after an
    # authoritative loader has returned the depot payload; a loader failure
    # never reaches this function.
    current_depot_ids = {
        int(depot_id)
        for item in _items(data.get("depots"))
        if (depot_id := _field(item, "depot_id")) is not None
    }
    unresolved_shared_ids = {
        int(depot_id)
        for depot_id in _items(data.get("unresolved_shared_depot_ids"))
        if depot_id is not None
    }
    await session.execute(delete(SteamAppDepot).where(SteamAppDepot.app_id == app_id))
    if current_depot_ids:
        await session.execute(
            delete(SteamDepotOs).where(SteamDepotOs.depot_id.in_(current_depot_ids))
        )
        # A missing shared source is explicitly non-authoritative.  Preserve
        # its old manifests until the source app becomes available again.
        manifest_refresh_ids = current_depot_ids - unresolved_shared_ids
        if manifest_refresh_ids:
            await session.execute(
                delete(SteamDepotManifest).where(
                    SteamDepotManifest.depot_id.in_(manifest_refresh_ids)
                )
            )
    for item in _items(data.get("depots")):
        depot_id = _field(item, "depot_id")
        if depot_id is None:
            continue
        row = await session.get(SteamDepot, int(depot_id))
        if row is None:
            row = SteamDepot(depot_id=int(depot_id))
            session.add(row)
        for field_name in (
            "name",
            "language",
            "architecture",
            "low_violence",
            "dlc_app_id",
            "optional_dlc_app_id",
            "depot_from_app",
            "shared_install",
            "system_defined",
        ):
            setattr(row, field_name, _field(item, field_name))
        if await session.get(SteamAppDepot, (app_id, int(depot_id))) is None:
            session.add(SteamAppDepot(app_id=app_id, depot_id=int(depot_id)))
    for raw in _items(data.get("depot_os")):
        if isinstance(raw, (tuple, list)) and len(raw) == 2:
            depot_id, operating_system = raw
        else:
            depot_id = _field(raw, "depot_id")
            operating_system = _field(raw, "os")
        if depot_id is not None and operating_system:
            key = (int(depot_id), str(operating_system))
            if await session.get(SteamDepotOs, key) is None:
                session.add(SteamDepotOs(depot_id=key[0], os=key[1]))
    for item in _items(data.get("manifests")):
        depot_id = _field(item, "depot_id")
        branch = _text(_field(item, "branch"))
        if depot_id is None or not branch:
            continue
        key = (int(depot_id), branch)
        manifest = await session.get(SteamDepotManifest, key)
        if manifest is None:
            manifest = SteamDepotManifest(depot_id=key[0], branch=key[1])
            session.add(manifest)
        manifest.manifest_id = _text(_field(item, "manifest_id"))
        manifest.download_size = _field(item, "download_size")
        manifest.disk_size = _field(item, "disk_size")
    await _gc_orphan_depots(session)


__all__ = ["persist_steam_scope", "remove_all_steam_data", "remove_steam_scope"]
