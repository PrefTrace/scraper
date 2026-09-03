# Игровой oracle: источники, поля и правила отбора

Проверка: 28.08.2026. Входы с пометкой **live** реально вернули данные в
JSON/wikitext; сценарии воспроизводятся
[скриптом](verify_game_data_sources.py). `P0` — включать сейчас, `P1` —
полезен, но transport либо matching требует предохранителя, `C` — endpoint и
auth-gate проверены, но нужен отдельный бесплатный account/key. Поле не
становится «фактом игры» только потому, что его отдал API.

## Границы модели: не склеивать разные вещи

| Узел | Примеры ключей | Что это |
|---|---|---|
| `game_work` | IGDB game ID, Wikidata QID, VNDB `v*` | Абстрактное произведение: серия, сюжет, персонажи, люди, оригинальная дата. |
| `release_edition` | IGDB release ID, VNDB `r*`, MobyGames platform release | Издание для платформы/региона/языка/носителя. |
| `store_listing` | Steam AppID, GOG product ID, Microsoft Store ID, Apple track ID | Текущая витрина и её условия; не равно `game_work`. |
| `store_offer` | Steam package/sub, Microsoft SKU, ITAD UUID, retailer deal ID | Цена/DRM/подписка/бандл в конкретной стране и моменте. |
| `company_brand` | source company ID + display name | «Valve Corporation» в storefront/credits; ещё не юридическое лицо. |
| `legal_entity` | LEI, CIK, Companies House number | Юрлицо с адресом, parent, filing и датами. |
| `observation` | `{source, metric, scope, observed_at}` | Рейтинг, CCU, owners estimate, цена, Proton tier, download count. Никогда не заменяет метаданные игры. |

## Рейтинги и счётчики: точная семантика

Слово «рейтинг» без источника, формулы и времени в oracle запрещено.

| Наблюдение | Сырые поля / формула | Что брать | Что **не** утверждать |
|---|---|---|---|
| **Steam review sentiment** | `appreviews.query_summary.total_positive`, `total_negative`, `total_reviews`; `positive_ratio = total_positive / total_reviews * 100`. Для Portal 2 live-ответ содержит 459901 / 6042 / 465943. | Все три count + вычисленный ratio, фильтры запроса (`language`, `review_type`, `purchase_type`), timestamp snapshot. | `query_summary.review_score` (целое 1–9) и `review_score_desc` — витринная категориальная метка, не процент; не хранить вместо counts. `num_reviews` — размер текущего ответа, не глобальный total. |
| **Steam review record** | `recommendationid`, `voted_up`, `votes_up`, `votes_funny`, `weighted_vote_score`, `comment_count`, refund/free/early-access/Deck flags; author playtime fields. | UGC-событие и его timestamp; дедупликация по `recommendationid`. | Не выводить из одного review общую оценку, продажи или демографию. SteamID/profile — персональные данные, не нужны для game graph. |
| **Steam Store `ratings`** | Например `ratings.usk.rating`, `ratings.dejus.required_age/descriptors`, `ratings.igrs.*`. | Возрастные классификации и descriptors, назвав authority. | Это не пользовательская/критическая оценка. |
| **Steam Store `metacritic`** | `metacritic.score` (0–100) и URL. Portal 2 live: `95`. | `critic_score` с `provider=metacritic`, `scope=PC listing`, URL как evidence. | Не называть Steam-рейтингом и не смешивать с user reviews. Steam не является первоисточником этого числа. |
| **Steam Store `recommendations.total`** | Один integer в appdetails. | Только отдельный store counter, если нужен для витрины. | Не заменяет `appreviews.total_reviews`: значения могут различаться. |
| **SteamSpy** | `positive`, `negative`, `userscore`, `owners` (диапазон-строка), `average_*`, `median_*`, `ccu`, tag votes. | Отдельный snapshot с source=`steamspy`; owners — `estimate_low/high`, если диапазон распарсился. | Не использовать как реальные продажи/MAU или как Steam's official count. |
| **IGDB / RAWG** | `total_rating`, `total_rating_count`, `aggregated_rating`, `aggregated_rating_count`, `rating`, `ratings_count` — после key. | Оставлять provider/type/count/scale отдельно. | Не усреднять с Steam/Metacritic/Speedrun. |
| **ProtonDB** | `tier`, `bestReportedTier`, `trendingTier`, `score`, `confidence`, `total`. | Linux compatibility snapshot + sample size. | Не «совместимость игры вообще» и не vendor certification. |
| **Speedrun** | verified run time, category, variables, platform, emulator flag, verification status. | Отдельные runs/leaderboards. | Нельзя получать «среднее время прохождения». Это соревновательная выборка. |

## P0 — первичные и точные источники

| Источник / transport | Ключ и matching | Извлекаемые поля | Брать в oracle | Не брать / причина |
|---|---|---|---|---|
| **Steam Store** — `GET store.steampowered.com/api/appdetails` **live** | `steam_appid`; запрос только с явными `l` и `cc`. | `type`, `name`, `required_age`, `is_free`, `controller_support`; `dlc[]`; descriptions, `website`; `developers[]`, `publishers[]`; `release_date`; `platforms`; `pc/mac/linux_requirements`; `supported_languages`; `categories[]`, `genres[]`; `price_overview`; `packages[]`, `package_groups[].subs[].packageid`; `screenshots[]`, `movies[]`; `achievements.total/highlighted`; `recommendations`; `metacritic`; age `ratings`; `content_descriptors`; support links. | Объект `steam_listing`, локализованное описание, Steam-declared company strings, Steam-only availability/features, storefront prices/packages, asset URLs, vendor requirements. | Не превращать developers/publishers strings в legal entity; не использовать HTML description/requirements как уже структурированные факты; не считать Steam release date глобальной первой датой игры; `categories/genres` оставить taxonomy=`steam`, не склеивать с другими. |
| **Steam Reviews** — `GET /appreviews/{appid}` **live** | AppID + cursor; `recommendationid` — key review. | См. таблицу рейтингов; также `language`, `review`, `timestamp_created/updated`, `steam_purchase`, `received_for_free`, `refunded`, `written_during_early_access`, `primarily_steam_deck`, `reactions`. | `review_aggregate_snapshot` и, только если действительно нужна аналитика текста, review events без лишних profile данных. | Не брать персональные `author.steamid`, `personaname`, avatar/profile URL в core oracle; не добавлять full review text без retention policy. |
| **Steam News** — `ISteamNews/GetNewsForApp/v0002` **live** | `gid`; AppID. | `title`, `url`, `is_external_url`, `author`, `contents`, `feedlabel`, `feedname`, `feed_type`, `date`. | Временную ленту announcement/patch note с типом и source URL. | Не помечать все публикации как «официальный патчноут»: endpoint включает external/RSS feeds. |
| **Steam CCU** — `ISteamUserStats/GetNumberOfCurrentPlayers` **live** | AppID. | `response.player_count`, `result`. | `concurrent_players` snapshot с `observed_at`. | Не строить историю без собственного sampling; не интерпретировать как owners/DAU. |
| **SteamSpy** — `steamspy.com/api.php?request=appdetails` **live** | AppID direct. | См. рейтинг; дополнительно `appid`, name, developer, publisher, price/initialprice/discount, tags→votes. | Независимое estimate/engagement observation и tag-vote taxonomy. | Company/name/price не приоритетнее Steam Store; owners всегда estimate. Rate-limit — консервативно 1 req/s. |
| **[VNDB Kana v2](https://api.vndb.org/kana)** **live** | Exact Steam join: `POST /release`, `filters=['extlink','=', ['steam', appid]]`; release `r*` → canonical VN `v*`. Live fixture: `698780 → r53689 → v21905`. | `release`: id/title/released, platforms, languages (`lang`, `main`, `mtl`), media, engine, `freeware`, patch, official, uncensored, voiced, minage, GTIN/catalog, `producers{id,name,developer,publisher}`, `extlinks`, `vns`. `vn`: original/alt/localized titles, original language, description, dev status, editions, release date, platforms/languages, developers, relations; rating/votecount/popularity/length; tags with `category/rating/spoiler`; staff role, characters, VA, screenshots. | Для VN — canonical work, edition/release, people/roles, content/technical tags, publishers/developers with explicit role, Steam exact link. | Не делать `not_found` ошибкой для обычной игры; spoiler-tag не применять как пользовательский content warning; VNDB rating — отдельная community metric. |
| **PCGamingWiki MediaWiki API** — `action=query&prop=revisions&rvslots=main&rvprop=content|ids|timestamp` **live** | Search Steam title через standard MediaWiki search/generator; у кандидата парсить infobox и принять только если primary `steam appid == AppID`. Live: поиск `Portal 2` дал 3 страницы; только pageid `194` содержит `steam appid = 620`. `steam appid side` — отдельные extra IDs, не match основной игры. | Wikitext recognised templates: `Infobox game` — cover, developer/publisher **с регионом**, engine, OS release dates, source cross-IDs (Steam/GOG/HLTB/Lutris/MobyGames/WineHQ), official site, licence; taxonomy (monetization/microtransactions/modes/pacing/perspective/controls/genres/art style/themes/series); `Availability`, `DRM`, `DLC`; `Game data` (config/save paths), cloud sync; `Video` (widescreen/multimonitor/ultrawide/4K/FOV/windowed/borderless/AA/upscaling/framegen/vsync/60/120fps/HDR/ray tracing + notes); `Input`, `Audio`, `VR support`, `Multiplayer`, `Middleware`, `Mods`, `API`, `Accessibility` where present. | Лучший source для PC technical capability/fixability: structured tri-state (`true/false/unknown/hackable`), per-feature notes, save/config/cloud paths, DRM/middleware, regional publisher and OS release evidence. Сохранять pageid/revid/timestamp и raw wikitext. | Cargo действительно закрыт (`permissiondenied`), но это не делает PCGW нерабочим. Не парсить rendered HTML. Не превращать prose `Fixbox`, refs, «known issues» в автоматические факты: это human text, неоднородно. `Reception` только как provider score reference, не как собственный рейтинг PCGW. Template parser держать whitelist и fail-closed при новом template. |
| **Wikidata** — WDQS `P1733` + `wbgetentities` **live** | Exact Steam locator: `?item wdt:P1733 "{appid}"`; Portal 2: `620 → Q279446`. Только QID, найденный через exact external ID, auto-link; title search — candidate. | Полный список разрешённых `P*`, правила качества и запреты — ниже в отдельной таблице. | Источник graph relations, historical company identity и external-ID bridges; raw statement evidence, qualifiers, rank/references/revision. | Не загружать и не материализовать «все claims и все связанные Q»; не использовать незаполненность как отрицательный факт; не делать Wikidata canonical для current storefront/price/requirements. |
| **[IsThereAnyDeal](https://docs.isthereanydeal.com/)** — public crosswalk **live** | `POST /lookup/id/shop/61/v1` body `["app/{appid}"]`; `app/620 → ITAD UUID`. Обратный mapping также live. | ITAD UUID, exact shop-object ↔ UUID; exact title lookup, slug/type/mature/assets при доступных routes. | High-confidence crosswalk Steam offer ↔ ITAD game, далее — key для deals/price source. | Title lookup — candidate only. Не использовать UUID как ID `game_work`: он представляет торговое aggregation. |
| **[CheapShark](https://apidocs.cheapshark.com/)** **live** | Search gives CheapShark `gameID`, then `GET /games?id=`; exact only when response `info.steamAppID` equals AppID. | `info.title/steamAppID/thumb`; `cheapestPriceEver.price/date`; each deal `storeID/dealID/price/retailPrice/savings`. | Cross-retailer price observation and historical low (one point). | Не полная price history и не полный список магазинов; price/retail/savings snapshot, не характеристика игры. |
| **[GOG catalog + v2 product](https://api.gog.com/v2/games/1207658924)** **live** | GOG numeric product ID; catalog pagination returns IDs/slugs. No Steam mapping in tested payload. | Product/store/support/forum links; title/type/global and store release dates/status; description/overview/copyright/technologies; developer/publisher; audio/text localisation; videos/screenshots/bonuses; OS requirements; tags/properties/features; ESRB/PEGI/USK rating + descriptors; editions/series; country price link. | GOG listing and DRM-free technical/store facts. | Endpoint работает, но не является обещанным public contract: raw HAL + schema fingerprint/contract test обязательны. Не match по title автоматически; rating boards здесь — store-declared classification, не review score. |
| **Microsoft Store / Xbox catalog** — `storeedgefd.../v9.0/products/{StoreId}` **live** | Exact Microsoft Store ID (`9NBLGGH2JHXJ` live); `market`, `locale`, `deviceFamily` — часть key. | Localized developer/publisher/description/support/website; features/categories, languages, requirements; ESRB/other rating descriptor + interactive elements; images/trailers; SKU/offer price/Game Pass eligibility; release/update/version/copyright. | Microsoft listing/offer + Xbox ecosystem facts. | Internal JSON contract: treat as P1 transport, keep raw and monitor schema. No title matching; StoreId получить из source link/IGDB external ID/manual mapping. |
| **Apple iTunes Search API** **live** | `trackId`; query `country`. | `artistId/artistName`, seller, bundle ID, title, description; release/current version dates/version/release notes; price/currency, rating/count, content rating/advisories, genres, languages, devices/min OS/file size, Game Center, artwork/screenshots. | iOS/macOS App Store listing/offer snapshot. | `artist` is storefront account, not proven legal company; rating remains provider-specific; no Steam title join. |

## P0/P1 — независимые тематические грани

| Источник | Поля | Решение |
|---|---|---|
| **ProtonDB summary** — `api/v1/reports/summaries/{appid}.json` **live** | `tier`, `bestReportedTier`, `trendingTier`, `score`, `confidence`, `total`. | Брать как `proton_compatibility_snapshot`. Не вытягивать «full reports» по придуманным API путям: проверенный `/reports/{appid}.json` дал `404`. |
| **[speedrun.com REST](https://github.com/speedruncomorg/api)** **live** | Game ID/name/aliases, release dates, platforms/regions/genres/engines/developers/publishers, ruleset/assets/series; category/level/variables; run time/date/platform/emulator/verification/video/splits/player; leaderboard. | Отдельный speedrun graph. Search by title → candidate; подтвердить release/platform/links. Не переносить их genres/developer в canonical без provider namespace. |
| **[Ludusavi manifest](https://github.com/mtkennerly/ludusavi-manifest)** **live** | Versioned YAML: store IDs, install dirs, launch executable/arguments/working dir, save/config files, registry, store/OS condition, Steam Cloud, `steamExtra`. Live Portal 2 contains `steam.id: 620`. | Брать technical backup/migration facts, commit SHA and YAML record. Не считать save path универсальной истиной и не смешивать `steamExtra` с primary listing. |
| **[GitHub REST](https://docs.github.com/rest)** **live** | repo/node IDs, owner, description, topics, visibility, default branch, license; tags/releases/assets/checksums, commits, languages, contributors, issues/PRs/security files. | Только когда `repo_url` подтверждён официальной страницей/подписанным release/developer. Тогда это лучший первичный source для open source game/tool/mod release history. Name search недопустим как связь. |
| **[Internet Archive Advanced Search](https://archive.org/advancedsearch.php)** **live** | Identifier, title, creator, year/date, collection, subject, mediatype; metadata endpoint даёт description/license/files/checksums/formats. | Preservation/archival copies with source evidence. Search output — candidates: принять только после platform/year/publisher/manual verification. |
| **[GLEIF LEI](https://www.gleif.org/en/lei-data/gleif-api)** **live** | LEI, legal name/form/status/jurisdiction/incorporation, registered/HQ addresses, registration/issuer/validation, BIC/MIC/OCID, direct and ultimate parents. | Authoritative legal-entity node after matching LEI; parent relationships are legal/dated facts. Name search itself не является match. |
| **[SEC EDGAR](https://www.sec.gov/search-filings/edgar-application-programming-interfaces)** **live** | CIK, legal name, ticker/exchange, SIC, EIN, LEI, website/investor URL, state/country, former names, filing history; `companyfacts` = reported XBRL facts. | Для US public companies — первичнее Wikidata в revenue/employees/acquisition/registrant facts. Нужен courteous User-Agent/contact; не покрывает private/non-US studios. |

## Wikidata: только белый список и уровни доверия

Текущий полный fan-out в репозитории — плохая стратегия: локальный demo для
одного Portal 2 загрузил 1,350 сущностей, 43,733 facts и 52,134 qualifiers.
Это не «богатая карточка игры», а в основном нерелевантная транзитивная сеть;
прошлый benchmark занял 122 s и 32 HTTP requests только для первого AppID.

### Измеренная заполненность, а не предположение

Прогнан WDQS по первым 100 AppID из `appids.txt`. Это **не случайная выборка**
и не оценка всего Steam; она нужна только показать, какие поля нельзя обещать
даже в уже хорошо представленном локальном cohort. Exact `P1733` нашёл все
100 AppID, но дал 102 QID: связь AppID↔QID иногда не 1:1. Таблица считает
прямые `wdt:` значения без deprecated statements; она **не** проверяет наличие
references, precision или истинность claim.

| Property | QID с полем / 102 | Значений | Вывод |
|---|---:|---:|---|
| `P31` instance of | 102 (100.0%) | 108 | Диагностика типа, не богатая мета. |
| `P400` platform | 102 (100.0%) | 439 | Хорошее historical/platform taxonomy, но не current support. |
| `P577` publication date | 102 (100.0%) | 183 | Полезная historical date; хранить precision и не затирать store date. |
| `P136` genre | 101 (99.0%) | 227 | Почти везде есть, но это отдельная taxonomy, не canonical genre. |
| `P407` language of work | 98 (96.1%) | 965 | Не соответствует списку локализаций/субтитров; не заменить Steam/GOG. |
| `P123` publisher | 87 (85.3%) | 114 | Умеренно полное independent role evidence. |
| `P178` developer | 86 (84.3%) | 98 | Умеренно полное independent role evidence. |
| `P1476` title | 49 (48.0%) | 54 | Не годится как обязательное display-title поле. |
| `P179` series | 39 (38.2%) | 40 | Ценная, но явно sparse структура. |
| `P86` composer | 22 (21.6%) | 37 | Supplemental only. |
| `P306` operating system | 13 (12.7%) | 31 | Не использовать для compatibility. |
| `P144` based on | 9 (8.8%) | 10 | Supplemental only. |
| `P155/P156` follows/preceded by | 7 (6.9%) / 5 (4.9%) | 7 / 6 | Не обещать franchise chronology. |
| `P725` voice actor | 7 (6.9%) | 52 | Полезно при наличии, не полный cast. |
| `P57/P58` director/screenwriter | 3 (2.9%) / 7 (6.9%) | 5 / 9 | Supplemental only. |
| `P2130/P2139` cost/revenue | 2 (2.0%) / 1 (1.0%) | 3 / 1 | Только raw sourced financial claim, не product field. |

Повторить измерение на другом cohort можно
[`audit_wikidata_coverage.py`](audit_wikidata_coverage.py) с реальным contact
в User-Agent. Скрипт намеренно измеряет availability отдельно от quality.

### Что синхронизировать

| Группа | Свойства | Решение и качество |
|---|---|---|
| Exact locator | `P1733` Steam application ID | **Hard link.** Принимать QID автоматически только этим способом (и другими direct external IDs, когда добавим). |
| Display / audit | labels, aliases, descriptions, `lastrevid`; statement rank, references, qualifiers | Брать всегда вместе с raw statement. Это не утверждение о предмете, а UI/доказательство/версия. |
| Work identity | `P31`, `P1476`, `P577` | `P31` — filter/diagnostic, не genre. `P1476` — title candidate/alias, не перезаписывает storefront name. `P577` — historical release date с precision; хранить как отдельный source claim, не затирать platform/store date. |
| Companies / credits | `P178` developer, `P123` publisher, `P57` director, `P50` author, `P86` composer, `P58` screenwriter, `P170` creator, `P287` designed by, `P162` producer, `P943` programmer, `P3080` game designer, `P767` contributor | Брать ребро `work —role→ QID` только с role/source/qualifiers/rank. Для developer/publisher storefront claim сохранять независимо: разные edition/region могут быть корректны одновременно. Promote to canonical role только при non-deprecated + reference или corroboration. |
| Structure | `P179` series + `P1545` ordinal, `P155` follows, `P156` followed by, `P144` based on | Один из лучших уникальных слоёв: хранить QID edges и qualifiers. Не подтягивать рекурсивно всю серию; по запросу/лимиту глубины 1. |
| Classification | `P400` platform, `P136` genre, `P407` language of work, `P306` OS | Полезны как **Wikidata taxonomy / historical release**. Не нормализовать union с Steam/IGDB/PCGW и не использовать как текущую поддержку платформы. |
| Gameplay / technology | `P1872` min players, `P1873` max players, `P408` engine, `P277` programming language, `P1547` dependency | `P408` engine — брать как independent evidence. Player count/technology — только referenced statement; слабая/неравномерная заполненность. |
| People / awards | `P674` characters + role qualifiers, `P725` voice actor + character qualifier, `P166` award, `P1411` nominated for | Брать только как granular sourced edges, не как complete cast/awards. VNDB приоритетнее в VN-части. |
| Commercial history | `P2664` units sold; `P2769` budget; `P2130` cost; `P2139` total revenue; monetary unit/time/point-in-time qualifiers | Хранить **только** non-deprecated statement с reference, amount+unit и date/precision. Не складывать разные periods/units; не подменять SEC reported financials. |
| External IDs | Все claims с datatype `external-id`, formatter URL | Брать в `external_identifier` с property ID и URL; это source link/candidate bridge, не подтверждение, что target record описывает ту же edition. |
| Company legal profile | `P1448` official name, `P1454` legal form, `P571/P576`, `P17/P740`, `P159` + `P580/P582`, `P749/P355`, `P112/P169/P488`, `P1128` + `P585`, identifiers | Брать как secondary candidate/evidence. GLEIF/SEC/Companies House выше по приоритету, когда есть direct key. |

### Что не продвигать из Wikidata

- Любой отсутствующий claim: это `unknown`, не `false`.
- `normal` statement без reference: хранить raw, но не публиковать как canonical.
- `deprecated` rank: не materialize, только оставить в raw audit при необходимости.
- Свойства вне whitelist и их Q-targets: не recursive sync. На Portal 2 это и породило десятки тысяч нерелевантных facts.
- `P136`/`P400`/`P407` нельзя использовать для перезаписи Steam/PCGW/IGDB taxonomy.
- Денежные/units-sold числа без периода, unit или reference — не брать в продуктовую витрину.

## PCGamingWiki: минимальный parser, а не Cargo и не HTML

| Шаг | Реализация |
|---|---|
| 1. Candidate pages | `action=query&generator=search&gsrsearch={Steam title}&gsrnamespace=0&prop=revisions...`; взять максимум 10. Можно сначала normalise trademark/edition suffix, но не считать это match. |
| 2. Exact validation | Выделить top-level `{{Infobox game ...}}`; принять page только при `steam appid` равном заданному AppID. `steam appid side` сохранять как edge `related_steam_listing`, а не identity. |
| 3. Parser | Нужен маленький balanced-brace parser для вызовов template + whitelist названий/именованных аргументов. Вложенные `Infobox game/row/*`, `Availability/row`, `Game data/*`, `DRM`, `Video`, `Input`, `Audio`, `VR support`, `Cloud` разбирать в typed fields. Никаких CSS selectors/rendered HTML. |
| 4. Versioning | Сохранять `pageid`, `revid`, `parentid`, `timestamp`, `raw_wikitext`, parser version. При изменении unknown template/argument не угадывать: diagnostic + raw. |
| 5. Facts | Для boolean-like capability хранить enum `supported | unsupported | unknown | hackable` + `notes` + source revision; не сплющивать `hackable` в `true`. |

## C — включать после бесплатной регистрации

| Источник | Поля после onboarding | Брать / не брать |
|---|---|---|
| **[IGDB](https://api-docs.igdb.com/)** | `games`, `external_games`, release dates/platform/region, involved companies + developer/publisher/porting/supporting role, franchise/collection/version/relations, genres/themes/keywords/modes/perspectives, engine, language support/multiplayer, age-rating descriptors, cover/artwork/screenshots/videos/websites, characters, ratings/follows/popularity. | Главный global game graph после Steam. Twitch application credentials обязательны, free только non-commercial. Hard match сначала через `external_games` Steam ID; title only candidate. |
| **[RAWG](https://rawg.io/apidocs)** | Listings, release/platform, tags/genres, developers/publishers/creators, store links, ESRB, requirements, DLC/series, screenshots/video, RAWG rating/count/playtime/player count, Metacritic link. | Good coverage/discovery, но агрегатор: использовать для candidate/enrichment, не как единственную истину. Key обязателен, free/hobby tier требует атрибуции и соблюдения terms. |
| **[TheGamesDB](https://api.thegamesdb.net/)** | game/platform/genre/developer/publisher/region/country, release, art/video; game unique ID and ROM hash lookup. | Очень полезен для retro ROM identity после live schema fixture. Все безключевые routes ответили `418`; сначала key и один hash test. |
| **[RetroAchievements](https://api-docs.retroachievements.org/)** | Console/game IDs, achievements/badges/points/authors/dates/unlock stats, leaderboards/tickets, game hashes/ROM filename/patch URL, user progress. | Unique exact ROM hash graph. Free account API key (`z`/`y`) обязателен; не хранить user data/progress без причины. |
| **[Nexus Mods](https://github.com/Nexus-Mods/Vortex/blob/master/packages/nexus-api-v3/schema/openapi.yaml)** | Game domain, mod/author/category/tag, description/version/date, downloads/endorsements, requirements, files/hashes, changelog, collections/update lists. | Official mod ecosystem; personal key free. Брать stable endpoints only and `game_domain` only after explicit mapping; `401` gate verified. |
| **[mod.io](https://docs.mod.io/)** | Game portal, mods/files/tags/dependencies, submitter/version/media/download stats/events. | Только для games that enable 3rd-party access; key + per-game capability. |
| **ITAD protected routes** | Search, current deals, regular/current price/cut/currency/expiry, DRM/platforms, history lows 1y/3m/all, waitlist webhooks. | UUID crosswalk уже P0; prices после registered app/key. Не изменять affiliate URLs, соблюдать API terms. |
| **[Companies House](https://developer.company-information.service.gov.uk/)** | UK number/name/type/status, incorporation/dissolution, SIC, address, officers, PSC, charges, filing history. | Legal source for UK entities after direct identity resolution; free API key, regional coverage only. |

## Не ставить в базовый pipeline

| Источник | Решение |
|---|---|
| **HLTB** | Текущий `howlongtobeatpy.async_search('Portal 2')` вернул `None`; library bootstraps dynamic endpoint/token from HTML/JS. Это не стабильный API. Если останется — только disabled best-effort with match confidence, никогда `not_found` как факт. |
| **Metacritic** | Текущий код slug-guesses и парсит HTML. Для oracle достаточно Store/IGDB reference URL/score как evidence; собственный HTML scraper не включать без разрешённого contract. |
| **MobyGames** | Rich (historical platform releases, companies, images, credits), но live API `401`; платные tiers, а full credits отдельно в Gold. Возможен research access, но до фиксированного free tier это не baseline. |
| **Giant Bomb** | Официальная страница сообщает, что games/companies/releases APIs сейчас недоступны. Старые SDK/wrappers не являются источником. |

## Конфликты: что побеждает и почему

| Факт | Победитель | Остальные |
|---|---|---|
| Цена, валюта, скидка, SKU/package, availability | Тот storefront и конкретные `country/locale`/timestamp | ITAD/CheapShark — retailer snapshots; никогда не overwrite store price. |
| Developer/publisher в конкретной продаже | Тот storefront/release | IGDB/VNDB/PCGW/Wikidata — independent role claims; могут различаться по region/port/edition и все быть верными. |
| Юрлицо, registration, parent, filings/финансы | GLEIF / SEC / Companies House по direct ID | Wikidata только secondary evidence. |
| PC configs, saves, cloud, feature support, DRM/middleware | PCGamingWiki for PC capability; store requirements as vendor minimum/recommended | Ludusavi — independent save/launch evidence; ProtonDB — Linux observation. Не выбирать один boolean вместо всех scopes. |
| Game work graph / relations | IGDB after exact external match; VNDB for VN; Wikidata referenced statements | Storefront не источник canon series/relations. |
| Genre/tag/theme | Не выбирать победителя: `source_taxonomy(source, id, label)` | Steam/IGDB/VNDB/PCGW semantics различны; mapping возможен отдельным curated layer, не union. |
| Ratings/engagement | Никто | Сохранять provider-specific observations по первой таблице. |

## Обязательные поля provenance

```text
source_record(source, record_type, source_id, canonical_url, retrieved_at,
              request_locale, request_country, source_revision, raw_payload_sha256)
source_fact(source_record_id, subject, predicate, object, qualifiers,
            rank, references, confidence, parser_version)
source_observation(source_record_id, metric, value, unit, denominator,
                   filters, observed_at)
match_candidate(left, right, method, evidence, confidence, decided_by)
```

Минимальные automatic joins: Steam AppID → Steam/SteamSpy/reviews/news/CCU;
Steam AppID → PCGW только после infobox validation; Steam AppID → ITAD/CheapShark/
Ludusavi/VNDB/Wikidata только через описанные exact fields. Всё остальное —
`match_candidate` до подтверждения.

## Проверка

```powershell
.\.venv\Scripts\python.exe analysis\verify_game_data_sources.py
```

Ожидаемые successful responses: Steam, SteamSpy, PCGamingWiki revision
wikitext, VNDB, Wikidata exact lookup, ITAD crosswalk, ProtonDB, speedrun,
Apple, GOG, Microsoft, CheapShark, GitHub, Internet Archive, GLEIF, SEC.
Ожидаемые gates: IGDB/RAWG/RetroAchievements/Nexus/ITAD prices.
