# Steam schema decisions against TZ.md

Wikidata is deprecated and is deliberately excluded from the current Steam
pipeline and benchmark. This file records only Steam schema decisions where
the public source does not expose the exact identity required by TZ.md.

## Organization credits

The public Steam app data exposes organization names, but not a stable
`organization_id`. The physical table therefore stores `organization_name`
instead of inventing a nullable or fake source ID. This is the explicit
source-driven variant of the TZ field; changing it to `organization_id`
requires a later TZ decision or another authoritative Steam source.

## Reviews

`recommendation_id` is retained as a source-identity extension for the Steam
review row. `review_type` is not persisted: positive/negative selection is
pipeline metadata, not a source-table column.

## Prices

`price_region` is populated only from an explicit region in the Steam source.
The request country/scope is not copied into a price row. Unknown source
regions use the empty string required by the current storage contract.

## Achievements

The physical identity is Steam `api_name` (with a numeric Steam ID only when a
structured source provides it). The public endpoints used by the benchmark
returned localized display names but no stable achievement ID/API name, so
those rows are skipped. A display name is never used as an achievement key.
