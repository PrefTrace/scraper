from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Sequence
from dataclasses import dataclass
from typing import Any

import httpx
from sqlalchemy import delete, select

from scraper.steam.client import SteamClient, SteamClientError
from scraper.steam.locales import (
    DEFAULT_STORE_COUNTRY,
    LocaleInfo,
    normalize_locales,
    normalize_store_country,
)
from scraper.steam.orm import SteamAppLocalization, SteamOrganizationCredit
from scraper.steam.parsers import (
    normalize_steam_type,
    parse_achievement_schema,
    parse_app_details,
    parse_appinfo_languages,
    parse_appinfo_semantics,
    parse_build_branches,
    parse_bundle_membership,
    parse_depots,
    parse_eulas,
    parse_external_links,
    parse_external_reviews,
    parse_global_achievement_percentages,
    parse_html_creator_entities,
    parse_html_structured_tags,
    parse_language_table,
    parse_package_metadata,
    parse_store_browse_item,
    parse_structured_genres,
    parse_structured_tags,
    parse_tags,
    parse_workshop_stats,
)
from scraper.steam.reviews import _collect_reviews, _fetch_summary
from scraper.steam.storage import (
    persist_steam_scope,
    remove_all_steam_data,
    remove_steam_scope,
)
from scraper.wikidata_deprecated.orm import SourceDiagnostic, SourceRefresh, utcnow

from .base import CachedSourceService, SourceLoadError


@dataclass(slots=True)
class SteamRefreshResult:
    """Current Steam refresh states plus people named by Steam."""

    refreshes: list[SourceRefresh]
    title: str | None
    developers: list[str]
    publishers: list[str]


class SteamGameSyncService(CachedSourceService):
    """Refresh Steam game substructures into source-specific ORM tables.

    The Steam AppID catalog is intentionally not part of this service. The
    existing ``fetch_app_ids`` flow remains the source of catalog IDs.
    """

    source = "steam"

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self._eligibility_checked: set[int] = set()

    async def ensure_schema(self) -> None:
        if self._schema_ready:
            return
        async with self._schema_lock:
            if not self._schema_ready:
                await self.database.create_schema(steam_only=True)
                self._schema_ready = True

    async def refresh(
        self,
        app_id: int,
        *,
        languages: Sequence[str] | None = None,
        store_country: str | None = DEFAULT_STORE_COUNTRY,
        positive_review_count: int = 4,
        negative_review_count: int = 4,
        review_pages: int | None = None,
        force: bool = False,
        client: httpx.AsyncClient | None = None,
    ) -> SteamRefreshResult:
        if app_id < 0:
            raise ValueError("Steam AppID cannot be negative")
        if positive_review_count < 0 or negative_review_count < 0:
            raise ValueError("Review counts cannot be negative")
        if review_pages is not None and review_pages < 1:
            raise ValueError("review_pages must be at least 1 when provided")
        locales = normalize_locales(languages)
        country = normalize_store_country(store_country)

        async def run(http: httpx.AsyncClient) -> SteamRefreshResult:
            steam = SteamClient(http)
            app_info_cache: dict[int, dict[str, Any]] = {}
            app_info_lock = asyncio.Lock()
            browse_cache: dict[str, dict[str, Any]] = {}
            browse_lock = asyncio.Lock()
            package_metadata_cache: dict[str, dict[int, dict[str, Any]]] = {}
            package_metadata_lock = asyncio.Lock()
            achievement_percentages: dict[str, float] | None = None
            achievement_percentages_lock = asyncio.Lock()
            category_registry: dict[int, str] | None = None
            category_registry_lock = asyncio.Lock()

            async def get_app_info() -> dict[str, Any]:
                async with app_info_lock:
                    if app_id not in app_info_cache:
                        app_info_cache[app_id] = await steam.public_app_info(app_id)
                    return app_info_cache[app_id]

            async def get_category_registry() -> dict[int, str]:
                nonlocal category_registry
                async with category_registry_lock:
                    if category_registry is None:
                        try:
                            loader = getattr(steam, "category_registry", None)
                            category_registry = (
                                await loader(api_key=self.config.steam_web_api_key)
                                if loader is not None
                                else {}
                            )
                        except (SteamClientError, httpx.HTTPError):
                            category_registry = {}
                    return category_registry

            async def get_store_browse(
                locale: LocaleInfo, *, bundle_id: int | None = None
            ) -> dict[str, Any]:
                loader = getattr(steam, "store_browse_items", None)
                if loader is None:
                    return {}
                cache_key = f"{locale.steam_language}:{country or 'US'}:{bundle_id or 'app'}"
                async with browse_lock:
                    if cache_key not in browse_cache:
                        try:
                            loader_kwargs: dict[str, Any] = {"store_country": country}
                            if bundle_id is not None:
                                loader_kwargs["bundle_id"] = bundle_id
                            browse_cache[cache_key] = await loader(app_id, locale, **loader_kwargs)
                        except (SteamClientError, httpx.HTTPError, TypeError):
                            browse_cache[cache_key] = {}
                    return browse_cache[cache_key]

            async def get_enriched_store_browse(locale: LocaleInfo) -> dict[str, Any]:
                browse = await get_store_browse(locale)
                if not browse:
                    return browse
                parsed = parse_store_browse_item(browse)
                bundles = parsed.get("bundles", [])
                memberships: dict[int, list[int]] = {}
                for bundle in bundles:
                    bundle_id = getattr(bundle, "bundle_id", None)
                    if bundle_id is None:
                        continue
                    bundle_payload = await get_store_browse(locale, bundle_id=int(bundle_id))
                    found_id, package_ids = parse_bundle_membership(bundle_payload)
                    if found_id is not None:
                        memberships[found_id] = package_ids
                enriched = dict(browse)
                enriched["_bundle_memberships"] = memberships
                return enriched

            async def get_package_metadata(package_ids: set[int], locale: LocaleInfo) -> list[Any]:
                if not package_ids:
                    return []
                cache_key = f"{locale.steam_language}:{country or 'US'}"
                async with package_metadata_lock:
                    cached = package_metadata_cache.setdefault(cache_key, {})
                    missing_ids = package_ids - set(cached)
                    if missing_ids:
                        try:
                            loader = getattr(steam, "package_details", None)
                            fetched = (
                                await loader(
                                    sorted(missing_ids),
                                    locale,
                                    store_country=country,
                                )
                                if loader is not None
                                else {}
                            )
                            if isinstance(fetched, dict):
                                cached.update(fetched)
                        except (SteamClientError, httpx.HTTPError, TypeError):
                            pass
                    return parse_package_metadata(
                        {
                            package_id: cached[package_id]
                            for package_id in package_ids
                            if package_id in cached
                        }
                    )

            async def enrich_package_metadata(
                parsed: dict[str, Any], locale: LocaleInfo
            ) -> dict[str, Any]:
                resolved_ids = {
                    int(item.package_id)
                    for item in parsed.get("editions", [])
                    if getattr(item, "package_id", None) is not None
                }
                bundle_ids = {
                    int(package_id)
                    for bundle in parsed.get("bundles", [])
                    for package_id in getattr(bundle, "edition_package_ids", [])
                }
                parsed["edition_metadata"] = await get_package_metadata(
                    bundle_ids - resolved_ids,
                    locale,
                )
                return parsed

            async def get_achievement_percentages() -> dict[str, float]:
                nonlocal achievement_percentages
                async with achievement_percentages_lock:
                    if achievement_percentages is None:
                        payload = await steam.global_achievement_percentages(
                            app_id,
                        )
                        achievement_percentages = parse_global_achievement_percentages(payload)
                    return achievement_percentages

            if app_id not in self._eligibility_checked:
                initial_app_info = await get_app_info()
                initial_common = initial_app_info.get("common")
                initial_common = initial_common if isinstance(initial_common, dict) else {}
                raw_type = initial_common.get("type")
                normalized_type = normalize_steam_type(raw_type)
                self._eligibility_checked.add(app_id)
                if raw_type not in (None, "") and normalized_type is None:
                    state = await self._persist(
                        app_id,
                        "eligibility",
                        data=None,
                        status="not_found",
                        error=(
                            "unsupported_app_type",
                            f"Steam app type is not indexed: {raw_type}",
                        ),
                    )
                    return SteamRefreshResult(
                        refreshes=[state], title=None, developers=[], publishers=[]
                    )

            operations: list[Awaitable[SourceRefresh]] = []
            detail_scopes: list[str] = []
            for locale in locales:
                locale_scope = locale.requested.replace(":", "_")
                country_scope = country or "none"
                details_scope = f"details:{locale_scope}:{country_scope}"
                detail_scopes.append(details_scope)
                store_scope = f"store:{locale_scope}:{country_scope}"
                achievement_scope = f"achievements:{locale_scope}"

                async def details_loader(locale: LocaleInfo = locale) -> object:
                    data = await steam.app_details(
                        app_id,
                        locale,
                        store_country=country,
                    )
                    requirements_data = data
                    if locale.steam_language != "english":
                        # System requirements are deliberately sourced from the
                        # English Store response even when localized game text
                        # is requested by the caller.
                        requirements_data = await steam.app_details(
                            app_id,
                            normalize_locales(["en-US"])[0],
                            store_country=country,
                        )
                        data = dict(data)
                        for english_key in (
                            "categories",
                            "content_descriptors",
                            "genres",
                            "ratings",
                            "ext_user_account_notice",
                            "drm_notice",
                        ):
                            if english_key in requirements_data:
                                data[english_key] = requirements_data[english_key]
                    app_info = await get_app_info()
                    registry = await get_category_registry()
                    browse = await get_enriched_store_browse(locale)
                    parsed = parse_app_details(
                        data,
                        locale,
                        app_id=app_id,
                        store_country=country,
                        requirements_data=requirements_data,
                        app_info=app_info,
                        store_browse=browse,
                        category_registry=registry,
                    )
                    parsed = await enrich_package_metadata(parsed, locale)
                    if locale.steam_language == "english":
                        parsed["global_authoritative"] = True
                    else:
                        # The requested locale owns only its localized row.
                        # Global app/package/requirements relations are still
                        # written from the explicit English response.
                        english_locale = normalize_locales(["en-US"])[0]
                        english_browse = await get_enriched_store_browse(english_locale)
                        global_data = parse_app_details(
                            requirements_data,
                            english_locale,
                            app_id=app_id,
                            store_country=country,
                            requirements_data=requirements_data,
                            app_info=app_info,
                            store_browse=english_browse,
                            category_registry=registry,
                        )
                        parsed["global_data"] = await enrich_package_metadata(
                            global_data,
                            english_locale,
                        )
                        parsed["global_authoritative"] = True
                    return parsed

                async def store_loader(locale: LocaleInfo = locale) -> object:
                    html = await steam.store_page(
                        app_id,
                        locale,
                        store_country=country,
                    )
                    metadata_html = html
                    if locale.steam_language != "english":
                        metadata_html = await steam.store_page(
                            app_id,
                            normalize_locales(["en-US"])[0],
                            store_country=country,
                        )
                    app_info = await get_app_info()
                    common = app_info.get("common") if isinstance(app_info, dict) else {}
                    common = common if isinstance(common, dict) else {}
                    registry = await get_category_registry()
                    semantics = parse_appinfo_semantics(
                        common,
                        registry=registry,
                        config=app_info.get("config")
                        if isinstance(app_info.get("config"), dict)
                        else None,
                    )
                    structured_languages = parse_appinfo_languages(
                        common.get("supported_languages")
                    )
                    browse = await get_store_browse(locale)
                    browse_data = parse_store_browse_item(browse)
                    workshop_html = collection_html = None
                    workshop_loader = getattr(steam, "workshop_page", None)
                    if workshop_loader is not None:
                        try:
                            workshop_html = await workshop_loader(app_id)
                        except (SteamClientError, httpx.HTTPError, TypeError):
                            workshop_html = None
                        try:
                            collection_html = await workshop_loader(app_id, section="collections")
                        except (SteamClientError, httpx.HTTPError, TypeError):
                            collection_html = None
                    tags, tag_localizations = parse_structured_tags(
                        common, language=locale.requested
                    )
                    if not tags:
                        tags, tag_localizations = parse_html_structured_tags(
                            metadata_html, language=locale.requested
                        )
                    genres, genre_localizations = parse_structured_genres(
                        common, language=locale.requested
                    )
                    supported_languages = (
                        structured_languages
                        or browse_data.get("supported_languages")
                        or parse_language_table(html)
                    )
                    diagnostics: list[dict[str, str]] = []
                    for item in supported_languages:
                        if getattr(item, "web_code", None):
                            continue
                        raw_language = getattr(item, "steam_language", None) or getattr(
                            item, "name", ""
                        )
                        diagnostics.append(
                            {
                                "code": "unknown_steam_language",
                                "message": (
                                    f"Steam language was not mapped to BCP47: raw={raw_language!r}"
                                ),
                            }
                        )
                    diagnostics.extend(
                        {
                            "code": "unknown_steam_category",
                            "message": f"Steam category ID has no registry name: {category_id}",
                        }
                        for category_id in semantics.get("unknown_category_ids", [])
                    )
                    diagnostics.extend(browse_data.get("price_diagnostics", []))
                    workshop = parse_workshop_stats(
                        common,
                        workshop_html,
                        collection_html=collection_html,
                        app_id=app_id,
                    )
                    if workshop.workshop_available and (
                        workshop.published_file_count is None or workshop.collection_count is None
                    ):
                        diagnostics.append(
                            {
                                "code": "steam_workshop_totals_unavailable",
                                "message": "Anonymous Workshop page did not expose complete totals",
                            }
                        )
                    return {
                        "supported_languages": supported_languages,
                        "diagnostics": diagnostics,
                        "tags": parse_tags(html),
                        "structured_tags": tags,
                        "tag_localizations": tag_localizations,
                        "genre_rows": genres,
                        "genre_localizations": genre_localizations,
                        "workshop_stats": workshop,
                        "organization_entities": parse_html_creator_entities(metadata_html),
                        "external_links": parse_external_links({}, metadata_html)
                        + list(browse_data.get("external_links", [])),
                        "external_reviews": parse_external_reviews({}, metadata_html),
                        "accessibility_features": semantics["accessibility_features"],
                        "deck_support": semantics["deck_support"],
                        "eulas": parse_eulas({"eulas": common.get("eulas")}),
                        "controllers": semantics["controllers"],
                    }

                async def achievement_loader(locale: LocaleInfo = locale) -> object:
                    api_key = self.config.steam_web_api_key
                    if not api_key:
                        raise SourceLoadError(
                            "failed",
                            "steam_api_key_missing",
                            "STEAM_WEB_API_KEY is required for structured achievements",
                        )
                    diagnostics: list[dict[str, str]] = []
                    try:
                        percentages = await get_achievement_percentages()
                    except SteamClientError:
                        # Some public apps expose a schema but no global
                        # percentage payload. Keep the structured achievement
                        # rows and make the missing enrichment observable.
                        percentages = {}
                        diagnostics.append(
                            {
                                "code": "steam_global_achievement_percentages_unavailable",
                                "message": (
                                    "Steam global achievement percentages were unavailable; "
                                    "achievement rows were stored with NULL global_percent"
                                ),
                            }
                        )
                    schema = await steam.achievement_schema(
                        app_id,
                        api_key=api_key,
                        language=locale.steam_language,
                    )
                    return {
                        "achievements": parse_achievement_schema(
                            schema,
                            language=locale.requested,
                            percentages=percentages,
                        ),
                        "diagnostics": diagnostics,
                    }

                operations.extend(
                    (
                        self._refresh_scope(
                            app_id,
                            details_scope,
                            details_loader,
                            force=force,
                        ),
                        self._refresh_scope(
                            app_id,
                            store_scope,
                            store_loader,
                            force=force,
                        ),
                    )
                )
                if self.config.steam_web_api_key:
                    operations.append(
                        self._refresh_scope(
                            app_id,
                            achievement_scope,
                            achievement_loader,
                            force=force,
                        )
                    )
                elif locale is locales[0]:

                    async def blocked_achievement_loader() -> object:
                        return {
                            "achievements": [],
                            "diagnostics": [
                                {
                                    "code": "steam_api_key_missing",
                                    "message": (
                                        "STEAM_WEB_API_KEY is required for structured achievements"
                                    ),
                                }
                            ],
                        }

                    operations.append(
                        self._refresh_scope(
                            app_id,
                            "achievements:blocked",
                            blocked_achievement_loader,
                            force=force,
                        )
                    )

                async def rating_loader(locale: LocaleInfo = locale) -> object:
                    return await _fetch_summary(
                        steam,
                        app_id,
                        locale=locale,
                        store_country=country,
                    )

                operations.append(
                    self._refresh_scope(
                        app_id,
                        f"rating:{locale_scope}:{country_scope}",
                        rating_loader,
                        force=force,
                    )
                )

            async def global_rating_loader() -> object:
                return await _fetch_summary(
                    steam,
                    app_id,
                    store_country=country,
                )

            operations.append(
                self._refresh_scope(
                    app_id,
                    f"rating:all:{country or 'none'}",
                    global_rating_loader,
                    force=force,
                )
            )

            async def positive_loader() -> object:
                return await _collect_reviews(
                    steam,
                    app_id,
                    review_type="positive",
                    count=positive_review_count,
                    pages=review_pages,
                    minimum_text_length=self.config.review_min_length_chars,
                )

            async def negative_loader() -> object:
                return await _collect_reviews(
                    steam,
                    app_id,
                    review_type="negative",
                    count=negative_review_count,
                    pages=review_pages,
                    minimum_text_length=self.config.review_min_length_chars,
                )

            operations.extend(
                (
                    self._refresh_scope(
                        app_id,
                        f"reviews:positive:{positive_review_count}:{review_pages or 'all'}",
                        positive_loader,
                        force=force,
                    ),
                    self._refresh_scope(
                        app_id,
                        f"reviews:negative:{negative_review_count}:{review_pages or 'all'}",
                        negative_loader,
                        force=force,
                    ),
                )
            )

            async def branches_loader() -> object:
                payload = await get_app_info()
                return {"branches": parse_build_branches(payload), **parse_depots(payload)}

            operations.append(
                self._refresh_scope(
                    app_id,
                    "branches",
                    branches_loader,
                    force=force,
                )
            )
            refreshes = list(await asyncio.gather(*operations))
            title, developers, publishers = await self._read_people(app_id, detail_scopes)
            return SteamRefreshResult(
                refreshes=refreshes,
                title=title,
                developers=developers,
                publishers=publishers,
            )

        if client is not None:
            return await run(client)
        timeout = httpx.Timeout(
            self.config.request_timeout_seconds,
            connect=self.config.connect_timeout_seconds,
        )
        limits = httpx.Limits(
            max_connections=self.config.concurrency,
            max_keepalive_connections=max(2, self.config.concurrency // 2),
        )
        async with httpx.AsyncClient(
            timeout=timeout,
            limits=limits,
            headers={"User-Agent": self.config.user_agent},
            follow_redirects=True,
        ) as http:
            return await run(http)

    async def _read_people(
        self,
        app_id: int,
        detail_scopes: Sequence[str],
    ) -> tuple[str | None, list[str], list[str]]:
        """Read the current developer/publisher facts after all scopes finish."""

        del detail_scopes
        async with self.database.session() as session:
            localizations = (
                await session.scalars(
                    select(SteamAppLocalization)
                    .where(SteamAppLocalization.app_id == app_id)
                    .order_by(SteamAppLocalization.language)
                )
            ).all()
            organizations = (
                await session.scalars(
                    select(SteamOrganizationCredit).where(SteamOrganizationCredit.app_id == app_id)
                )
            ).all()

        title = localizations[0].name if localizations else None
        developers: list[str] = []
        publishers: list[str] = []
        for organization in organizations:
            if organization.status == "developer":
                _append_unique(developers, organization.credited_name)
            elif organization.status == "publisher":
                _append_unique(publishers, organization.credited_name)
        return title, developers, publishers

    async def _persist(
        self,
        app_id: int,
        scope: str,
        *,
        data: object | None,
        status: str,
        error: tuple[str, str] | None = None,
    ) -> SourceRefresh:
        """Persist Steam rows without routing them through legacy SourceFact."""

        await self.ensure_schema()
        observed_at = utcnow()
        async with self._write_lock:
            async with self.database.session() as session:
                state = await session.get(
                    SourceRefresh,
                    {"source": self.source, "steam_app_id": app_id, "scope": scope},
                )
                if state is None:
                    state = SourceRefresh(
                        source=self.source,
                        steam_app_id=app_id,
                        scope=scope,
                    )
                    session.add(state)
                state.checked_at = observed_at
                state.status = status
                state.last_error = error[1] if error else None
                if status == "not_found":
                    await remove_all_steam_data(session, app_id)
                elif status == "ready":
                    await remove_steam_scope(session, app_id, scope, data)
                    await persist_steam_scope(session, app_id, scope, data)
                    state.refreshed_at = observed_at
                await session.execute(
                    delete(SourceDiagnostic).where(
                        SourceDiagnostic.source == self.source,
                        SourceDiagnostic.steam_app_id == app_id,
                        SourceDiagnostic.scope == scope,
                    )
                )
                if error:
                    session.add(
                        SourceDiagnostic(
                            source=self.source,
                            steam_app_id=app_id,
                            scope=scope,
                            code=error[0],
                            message=error[1],
                            observed_at=observed_at,
                        )
                    )
                if status == "ready" and isinstance(data, dict):
                    for diagnostic in data.get("diagnostics", []):
                        if not isinstance(diagnostic, dict):
                            continue
                        session.add(
                            SourceDiagnostic(
                                source=self.source,
                                steam_app_id=app_id,
                                scope=scope,
                                code=str(diagnostic.get("code") or "steam_diagnostic"),
                                message=str(diagnostic.get("message") or ""),
                                observed_at=observed_at,
                            )
                        )
                await session.commit()
                return state


def _append_unique(values: list[str], value: str) -> None:
    if value not in values:
        values.append(value)


__all__ = ["SteamGameSyncService", "SteamRefreshResult"]
