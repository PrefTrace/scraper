# PrefTrace source mapping

Это source-close mapping, а не объединённая модель и не entity resolution.
Для Steam сохраняем нормализованные факты Steam в отдельных source-specific
таблицах. `tz_contract.py` независимо проверяет физическую SQLite-схему.

## Steam application

`steam_apps`:

```text
app_id PK
type, demo_id, dlc_for_app_id, optional_dlc, required_app_id
linux_build, windows_build, mac_build, vac_enabled
required_age nullable                 # raw/global Steam required age
metacritic_score, metacritic_url
gamepad_preferred nullable             # authoritative AppInfo: true/false
controller_support nullable            # none/partial/full; NULL only if source unavailable
release_date, release_date_max, release_status
external_account_notice, drm_notice
```

`steam_app_localizations` stores `(app_id, language)` with `name`, short/about,
long description and legal notice. Languages are BCP47.

`steam_media` stores canonical media types (`screenshot`, `trailer`, `header_capsule`,
`small_capsule`, `main_capsule`, `vertical_capsule`, `page_background`, library
asset classes), URL, format and an explicit source language. Global media language
is NULL. `capsule_184x69` is not Main Capsule.

## Editions, prices and restrictions

`steam_editions(package_id PK, name nullable, description nullable)` has no ETL
`resolved` flag. Missing public package metadata remains NULL and is visible in
refresh/diagnostic state.

`steam_app_editions(app_id, package_id)` is the app-to-package relation.

`steam_edition_prices` has:

```text
package_id, price_region PK
currency nullable
initial, final, discount_percent
discount_description nullable, discount_end_at nullable
price_type, period, period_units
```

`steam_bundle_prices` is analogous with `(bundle_id, price_region)` as its PK.
Currency is not a region. A missing price row is not a country restriction.

`steam_bundles(bundle_id PK, name, discount_percent, must_purchase_as_set)` keeps
static bundle configuration. `steam_bundle_prices.effective_discount_percent` is
the current effective saving derived from the purchase option's initial/final
prices, not a copy of the static field. Current memberships are stored in
`steam_bundle_editions(bundle_id, package_id)`.

`steam_package_country_restrictions(package_id, restriction_type, country_code)`
stores only explicit Steam/package restrictions with normalized ISO country codes.
It does not infer restrictions from price availability or store country.

StoreBrowse package prices use `original_price_in_cents -> initial`,
`final_price_in_cents -> final`, and `discount_pct -> discount_percent`.
`initial_price_in_cents` is not a source field for this mapping. A non-zero source
discount never fabricates `initial=final`. Filled initial/final/discount values
are validated and inconsistent values produce a diagnostic.

## Ratings and descriptors

`steam_age_ratings` stores `(age_id PK, app_id, standard, rating_generated,
use_age_gate, banned, rating, minimum_age, descriptor_raw)`. `minimum_age` is
authority-specific: use source `required_age` first, then only unambiguous
authority mappings (for example PEGI 7 -> 7). Do not copy app required age into
every authority.

`steam_descriptors` stores normalized fragments, preserving `steam_id` where the
source supplies one. `descriptor_raw` remains lossless in the rating row.
Official Steam names such as `Frequent Violence or Gore` are passed through the
same multilingual normalizer and may produce `frequent violence` and `gore`.
Normalization preserves separators, removes only title boilerplate and metadata
fragments, keeps negations, and uses `simplemma` plus `stopwordsiso` when
available. It never discards a whole block because one line is metadata.

## Steam capabilities

`steam_features` and `steam_accessibility_features` store category IDs and English
registry names. Unknown IDs remain with NULL names and a diagnostic.
`steam_deck_support(app_id PK, status)` uses unknown/unsupported/playable/supported.
`steam_eulas`, `steam_external_links`, `steam_external_reviews` keep source facts.

`steam_controllers(app_id, controller PK, bluetooth nullable, usb nullable)` has no
`support` column: a row itself means Steam reported the controller/configuration.
Unknown transport remains NULL.

## Organizations

`steam_organizations(creator_clan_account_id PK, slug, name, homepage,
follower_count, logo_url, background_url)` is the canonical Steam creator entity
when the public source supplies the identity.

`steam_organization_credits(app_id, status, creator_clan_account_id nullable,
credited_name)` keeps the source display string independently. Minimum roles are
`developer` and `publisher`; no fuzzy cross-source resolution is performed.

## Languages, tags and genres

`steam_supported_languages(app_id, language PK)` stores nullable `audio`, `text`
and `subtitles`; structured AppInfo false is known false, weak-source unknown is
NULL. `steam_review_language_stats` uses RFC 4647 BCP47 language codes and the
literal `*` for ALL languages; ALL is not nullable.

`steam_tags(app_id, tag_id PK, weight nullable)` and
`steam_tag_localizations(tag_id, language PK, name)` store Steam tag IDs, weights
and localized names. `steam_genres(app_id, genre_id PK)` and
`steam_genre_localizations(genre_id, language PK, name)` do the same for genres.
Tags and genres are distinct relations; English is an ordinary `en` localization.

## Depots and branches

`steam_depots(depot_id PK, name, language, architecture, low_violence,
dlc_app_id, optional_dlc_app_id, depot_from_app, shared_install, system_defined)`
stores source depot facts. `steam_app_depots(app_id, depot_id)` is a separate
relation because shared depots can be used by another app. `steam_depot_os` stores
one `(depot_id, os)` row per `oslist` value; missing restrictions are not expanded
to every known OS. `steam_depot_manifests(depot_id, branch, manifest_id,
download_size nullable, disk_size nullable)` stores depot-level public metadata,
not file/chunk contents.

`steam_build_branches(app_id, name PK, updated_at, description, build_id)` keeps
derived min/median/max download and disk sizes. A profile with an unknown depot
download (or disk) is excluded from that metric independently; unknown values are
never added as zero. Valid shared depots participate in base install profiles;
DLC-gated depots do not.

## Workshop, reviews and achievements

`steam_workshop_stats(app_id PK, workshop_available, published_file_count,
collection_count)` is filled only from anonymous/public AppInfo/pages. If totals
are unavailable anonymously they remain NULL with a diagnostic.

`steam_reviews` stores the selected review sample and its stable
`recommendation_id`, author/playtime/vote fields, `datetime_dev_responded`,
response text and language. Sampling is intentional: the pipeline stores the
configured positive/negative sample, not all reviews. `steam_achievements` and
`steam_achievement_localizations` use the structured Web API schema path;
percentages are optional enrichment.

## Common invariants

Empty strings normalize to NULL. Boolean NULL means unknown, false means known
negative. Numeric zero remains a real zero. Refresh replacement is current-state
replacement: `(source, app_id, scope, code)` diagnostics are upserted, so repeated
identical refreshes do not duplicate them. Shared packages/bundles are garbage
collected only after all app references are checked.

Wikidata and other non-Steam sources remain deprecated and are outside this
Steam mapping iteration.
