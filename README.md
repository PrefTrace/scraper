# Steam game data scraper

Асинхронный набор source-сервисов. Сервисы не возвращают агрегированный JSON:
они получают AppID, проверяют TTL в SQLite и сохраняют обновлённые значения в
ORM-таблицы `SourceRefresh`, `SourceFact` и `SourceDiagnostic`.

## Каталог Steam AppID

Существующий метод получения каталога AppID оставлен без изменения:

```python
import asyncio

from scraper import get_app_ids


async def main() -> None:
    app_ids = await get_app_ids(
        api_key="STEAM_WEB_API_KEY",
        include_games=True,
        include_dlc=True,
        include_software=True,
        include_videos=True,
        include_hardware=True,
    )
    print(len(app_ids), app_ids[:10])


asyncio.run(main())
```

## ORM-сервисы

Внутри приложения pipeline сам создаёт задачи. Субмодуль задачу не создаёт и
не оборачивает вызов синхронной функцией:

```python
import asyncio

from scraper import (
    PCGamingWikiSyncService,
    ScraperConfig,
    ScraperDatabase,
    SteamGameSyncService,
    SteamSpySyncService,
    WikidataSyncService,
)


async def main() -> None:
    database = ScraperDatabase(ScraperConfig.from_env())
    try:
        wikidata = WikidataSyncService(database)
        pcgw = PCGamingWikiSyncService(database)
        steam = SteamGameSyncService(database)
        steamspy = SteamSpySyncService(database)

        steam_result = await asyncio.create_task(steam.refresh(620))
        await asyncio.gather(
            asyncio.create_task(
                wikidata.refresh_game(
                    620,
                    developers=steam_result.developers,
                    publishers=steam_result.publishers,
                )
            ),
            asyncio.create_task(pcgw.refresh(620)),
            asyncio.create_task(steamspy.refresh(620)),
        )
    finally:
        await database.dispose()


asyncio.run(main())
```

`WikidataSyncService`, `SteamGameSyncService`, `HltbSyncService`,
`MetacriticSyncService`, `PCGamingWikiSyncService` и `SteamSpySyncService` используют один общий
TTL-кеш в базе. Повторный вызов свежего scope не обращается к внешнему
источнику. Блокировки на AppID/scope защищают от дублирования запросов при
конкурентных задачах.

Steam является первичным источником для цепочки обогащения. Результат
`SteamGameSyncService.refresh()` содержит текущие списки `developers` и
`publishers`; pipeline передаёт их в `WikidataSyncService.refresh_game()`.
Если Wikidata не находит игру по AppID, сервис всё равно ищет организации по
этим именам, кеширует поиск по TTL и связывает найденные Q-ID с AppID игры.

## Wikidata

Wikidata ищется напрямую по Steam AppID через `P1733`, затем дочитываются
сущности и claims. ORM хранит нормализованные сущности, факты, qualifiers и
связи. Поддерживаются данные игры и организаций: франшиза, соседние игры,
основанное произведение, игроки, персонажи и роли, создатели, voice actors,
награды, бюджет, продажи с датами, язык программирования, движок, технологии,
внешние IDs, а также профиль организаций, финансы и штат как временной ряд.

Поля `languages` и `game_mechanics` в результат Wikidata не входят.

## PCGamingWiki

`PCGamingWikiSyncService` получает страницу игры по AppID. Сначала используется
официальный AppID redirect endpoint; если он недоступен, выполняется MediaWiki
поиск по переданному названию и проверка AppID в `Infobox game`. В ORM попадают
заголовок и URL страницы, Steam AppID, cover, разработчики, издатели, движки,
релизы, внешние IDs, секции и поля инфобокса как адресуемые scalar-факты.

Для PCGamingWiki действует отдельный межзапросный интервал по умолчанию 2.1
секунды:

```text
SCRAPER_PCGAMINGWIKI_MIN_INTERVAL_SECONDS=2.1
```

## SteamSpy

`SteamSpySyncService` сохраняет в scope `stats` только согласованный набор:

- `app_id`;
- `owners_min` и `owners_max`;
- среднее и медианное время игры за всё время и за последние 2 недели, в минутах;
- `ccu`;
- теги с голосами по путям `tags.<tag>`.

Оценки SteamSpy не являются официальными продажами или точным числом игроков.
Для обычных запросов используется ограничитель
`SCRAPER_STEAMSPY_MIN_INTERVAL_SECONDS` с дефолтом 1 секунда. Массовый endpoint
каталога SteamSpy в этот source не входит; список AppID остаётся за существующим
Steam-методом.

## Конфигурация

Используются только общие переменные окружения:

```text
SCRAPER_DATABASE_URL=sqlite+aiosqlite:///./scraper.sqlite3
SCRAPER_TTL_SECONDS=2592000
SCRAPER_CONCURRENCY=32
SCRAPER_BATCH_SIZE=50
SCRAPER_REQUEST_TIMEOUT_SECONDS=35
SCRAPER_CONNECT_TIMEOUT_SECONDS=15
SCRAPER_USER_AGENT=game-scraper/1.0
SCRAPER_PCGAMINGWIKI_MIN_INTERVAL_SECONDS=2.1
SCRAPER_STEAMSPY_MIN_INTERVAL_SECONDS=1.0
```

## Проверки и демо

```powershell
.\.venv\Scripts\python.exe -m pip install -r requirements-dev.txt
.\.venv\Scripts\pytest.exe -q
.\.venv\Scripts\ruff.exe check scraper tests
.\.venv\Scripts\mypy.exe scraper
```

В `demo/` находятся измерения Wikidata, PCGamingWiki и SteamSpy, а также примеры
ORM-выгрузки. Для PCGamingWiki:

```powershell
.\.venv\Scripts\python.exe demo\benchmark_pcgamingwiki.py --app-id 620 --title "Portal 2"
```

Скрипт сравнивает принудительное обновление с повторным вызовом при свежем TTL.
Каталог AppID для демо по-прежнему берётся существующим Steam-путём.

Для SteamSpy аналогичный замер запускается так:

```powershell
.\.venv\Scripts\python.exe demo\benchmark_steamspy.py --app-id 620
```
