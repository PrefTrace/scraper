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
is NULL. Rows are deduplicated by canonical physical asset URL/language, so a URL
cannot be both `main_capsule` and `small_capsule`; `capsule_184x69` is not Main
Capsule.

## Editions and regional price observations

`steam_editions(package_id PK, name nullable, description nullable)` has no ETL
`resolved` flag. Missing public package metadata remains NULL and is visible in
refresh/diagnostic state.

`steam_app_editions(app_id, package_id)` is the app-to-package relation.

`steam_edition_prices` has:

```text
package_id, price_region PK
currency nullable
initial, final, discount_percent
discount_type nullable, discount_end_at nullable
regional_edition nullable, run_region_restricted nullable
price_type, period, period_units
```

`steam_bundle_prices` is analogous with `(bundle_id, price_region)` as its PK.
It also uses `discount_type`, not a localized discount description. Known source
tokens normalize as `#discount_desc_preset_special -> special` and
`#discount_desc_preset_launch -> launch`; an unknown token is retained raw.
`discount_end_at` comes only from Steam's actual `discount_end_date` field.

`price_region` is an explicitly requested observation region, never a currency
or an inferred country. For editions:

```text
row absent                         -> region/package was not observed
row + initial=NULL + final=NULL    -> observed, package has no sale price
row + final non-NULL               -> observed sale/free price
```

`initial=0, final=0, discount_percent=NULL` means permanent free. A temporary
free promotion remains `initial>0, final=0, discount_percent=100` only when
Steam reports that promotion. Currency stays nullable, including for free rows.

`regional_edition` is derived only from authoritative package/depot content
topology: true/false when that topology is sufficient, NULL otherwise. It is
never inferred from price, currency or purchase restrictions.
`run_region_restricted` is an experimental package-runtime result relative to
this observed region. It is true/false only when authoritative anonymous
package metadata clearly permits the conclusion; otherwise NULL with a
diagnostic. Country lists and purchase restrictions are not persisted.

`steam_bundles(bundle_id PK, name, discount_percent, must_purchase_as_set)` keeps
static bundle configuration. `steam_bundle_prices.effective_discount_percent` is
the current effective saving derived from the purchase option's initial/final
prices, not a copy of the static field. Current memberships are stored in
`steam_bundle_editions(bundle_id, package_id)`. When source bundle metadata
definitely reports no configured discount, static `discount_percent` is 0 rather
than NULL.

StoreBrowse package prices use `original_price_in_cents -> initial`,
`final_price_in_cents -> final`, and `discount_pct -> discount_percent`.
`initial_price_in_cents` is not a source field for this mapping. A non-zero source
discount never fabricates `initial=final`. Filled initial/final/discount values
are validated and inconsistent values produce a diagnostic.

## Ratings and descriptors

`steam_age_ratings` stores `(age_id PK, app_id, standard, rating_generated,
use_age_gate, banned, rating, minimum_age, descriptor_raw)`. `minimum_age` is
source `required_age`, otherwise `int(rating)` only if the rating is fully
numeric; otherwise NULL. No authority-specific labels such as ESRB `M` are
invented. Thus USK `"6"` is 6.

`steam_descriptors` stores normalized fragments, preserving `steam_id` where the
source supplies one. `descriptor_raw` remains lossless in the rating row.
Official Steam names such as `Frequent Violence or Gore` are passed through the
same multilingual normalizer and may produce `frequent violence` and `gore`.
Normalization preserves separators, removes only title boilerplate and metadata
fragments, keeps negations, and uses `simplemma` plus `stopwordsiso` when
available. It never discards a whole block because one line is metadata.

## Steam capabilities

`steam_features(app_id, category_id)` and
`steam_accessibility_features(app_id, category_id)` are relations only.
`steam_category_localizations(category_id, language, name)` stores localized
registry names with BCP47 languages; English is the ordinary `en` row. Unknown
category relations remain stored, with no localization and a diagnostic.
With authoritative AppInfo categories, VAC category 8 means true and its absence
means false; NULL is reserved for unavailable AppInfo.
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
`developer` and `publisher`; IDs and names are taken from StoreBrowse
`basic_info.developers/publishers`, never fuzzy matched. A known clan ID is
anonymously enriched from its Creator Home page; a failed enrichment leaves a
partial organization and diagnostics, not a lost credit.

## Languages, tags and genres

`steam_supported_languages(app_id, language PK)` stores nullable `audio`, `text`
and `subtitles`; structured AppInfo false is known false, weak-source unknown is
NULL. `steam_review_language_stats` uses RFC 4647 BCP47 language codes and the
literal `*` for ALL languages; ALL is not nullable.

`steam_tags(app_id, tag_id PK, weight nullable)` and
`steam_tag_localizations(tag_id, language PK, name)` store Steam tag IDs, weights
and localized names. Tag IDs/weights come from structured StoreBrowse tags, not
an AppInfo map index. `steam_genres(app_id, genre_id PK)` and
`steam_genre_localizations(genre_id, language PK, name)` do the same for genres.
Localized detail scopes add genre/tag/category names where Steam exposes them.
Tags and genres are distinct relations; English is an ordinary `en` localization.

## Depots and branches

`steam_depots(depot_id PK, name, language, architecture, low_violence,
dlc_app_id, optional_dlc_app_id, depot_from_app, shared_install, system_defined)`
stores source depot facts. AppInfo `config.language` is normalized with the Steam
language mapper to BCP47 (unknown raw values are NULL plus diagnostics); live
source keys include `optionaldlc` and `systemdefined`. `steam_app_depots(app_id, depot_id)` is a separate
relation because shared depots can be used by another app. `steam_depot_os` stores
one `(depot_id, os)` row per `oslist` value; missing restrictions are not expanded
to every known OS. `steam_depot_manifests(depot_id, branch, manifest_id,
download_size nullable, disk_size nullable)` stores depot-level public metadata,
not file/chunk contents.

`steam_build_branches(app_id, name PK, updated_at, description, build_id)` keeps
derived min/median/max download and disk sizes over actual install profiles:
common depots plus compatible OS, architecture and language depots. An unknown
required depot makes that metric unknown for the profile independently; it is
never added as zero. Every `oslist` value participates. Shared depots are read
from cached source-AppInfo when public; unresolved shared relations persist but
make profile size unknown. DLC-gated depots do not participate.

## Workshop, reviews and achievements

`steam_workshop_stats(app_id PK, workshop_available, published_file_count,
collection_count)` is filled only from anonymous/public AppInfo/pages. If totals
are unavailable anonymously they remain NULL with a diagnostic. A Workshop page
total is never copied into `collection_count`; Workshop category signals include
both known IDs 30 and 33.

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
