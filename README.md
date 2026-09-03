# Steam game data scraper

Асинхронный набор source-сервисов. Сервисы не возвращают агрегированный JSON:
они получают AppID, проверяют TTL в SQLite и сохраняют обновлённые значения в
операционные таблицы `SourceRefresh`/`SourceDiagnostic` и source-specific ORM-
таблицы соответствующего источника. Steam хранится в таблицах `steam_*`, а
универсальный `SourceFact` оставлен только для legacy/Wikidata-совместимости.

Требования разделены по границам сервисов:

- [Scraper Service](TECHNICAL_SPEC_SCRAPER_SERVICE.md) — очереди, TTL,
  источники и промежуточное ORM-состояние;
- [Materializer Service](TECHNICAL_SPEC_MATERIALIZER_SERVICE.md) — каноническая
  PostgreSQL-модель, идентичности сущностей и четыре разрешённых вектора.

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

## Pipeline и очереди

В первой версии feeder читает AppID из файла, указанного в
`SCRAPER_APPIDS_FILE`. Очереди находятся в памяти процесса. Для каждого
источника запускается один асинхронный worker. Сами source-сервисы задачи не
создают: после завершения Steam pipeline регистрирует независимые задачи
Wikidata для игры, разработчиков и паблишеров.

```python
import asyncio

from scraper import (
    PipelineServices,
    ScraperConfig,
    ScraperDatabase,
    ScraperPipeline,
    SteamGameSyncService,
    WikidataSyncService,
)


async def main() -> None:
    config = ScraperConfig.from_env()
    if config.app_ids_file is None:
        raise RuntimeError("SCRAPER_APPIDS_FILE is required")
    database = ScraperDatabase(config)
    try:
        pipeline = ScraperPipeline(
            PipelineServices(
                steam=SteamGameSyncService(database),
                wikidata=WikidataSyncService(database),
            )
        )
        # Для тестового запуска используется ограниченный префикс файла.
        await pipeline.run_from_file(config.app_ids_file, limit=10)
    finally:
        await database.dispose()


asyncio.run(main())
```

`WikidataSyncService` и `SteamGameSyncService` используют общий TTL-кеш в базе.
Очередь хранит только текущие задачи в памяти; факты, TTL и результаты
источников сохраняются в ORM.

Steam является первичным источником для цепочки обогащения. Результат
`SteamGameSyncService.refresh()` возвращает текущие списки `developers` и
`publishers`; pipeline одновременно ставит в очередь задачу Wikidata-игры и
отдельные задачи поиска организаций по каждому имени. Если Wikidata не
находит игру по AppID, отдельные задачи организаций всё равно выполняются.
Поиск имени кешируется по TTL, а полная загрузка каждой найденной Q-сущности
идёт отдельной задачей.

## Wikidata

Wikidata ищется напрямую по Steam AppID через `P1733`, затем дочитываются
сущности и claims. ORM хранит нормализованные сущности, факты, qualifiers и
связи. Поддерживаются данные игры и организаций: франшиза, соседние игры,
основанное произведение, игроки, персонажи и роли, создатели, voice actors,
награды, бюджет, продажи с датами, язык программирования, движок, технологии,
внешние IDs, а также профиль организаций, финансы и штат как временной ряд.

Поля `languages` и `game_mechanics` в результат Wikidata не входят.

## PCGamingWiki (deprecated)

`PCGamingWikiSyncService` оставлен отдельным импортируемым модулем, но помечен
deprecated и исключён из `ScraperPipeline`: он не имеет очереди, worker’а и не
получает задач после завершения Steam. Cargo-клиент будет доработан отдельно.

## Deprecated-источники

Steam — единственный активный parser в pipeline. `steamspy_deprecated`,
`hltb_deprecated`, `metacritic_deprecated` и `pcgamingwiki_deprecated` вынесены
в явно переименованные подпакеты/модули и не имеют очередей в pipeline.

## Steam

`SteamGameSyncService` сохраняет локализованную информацию и поддерживаемые
языки, диапазон даты выхода, платформы, системные требования на английском,
медиа, издания и цены, бандлы, внешние ссылки, возрастные рейтинги и
дескрипторы, фичи, Steam Deck, EULA, контроллеры, разработчиков/издателей,
публичные ветки сборок из AppInfo, статистику языков отзывов, отзывы и
достижения. Ветки с паролем не сохраняются. Сбор CCU по ТЗ на этом этапе не
выполняется.

## Конфигурация

Используются только общие переменные окружения:

```text
SCRAPER_DATABASE_URL=sqlite+aiosqlite:///./scraper.sqlite3
SCRAPER_APPIDS_FILE=./appids.txt
SCRAPER_TTL_SECONDS=2592000
SCRAPER_CONCURRENCY=32
SCRAPER_BATCH_SIZE=50
SCRAPER_REQUEST_TIMEOUT_SECONDS=35
SCRAPER_CONNECT_TIMEOUT_SECONDS=15
SCRAPER_USER_AGENT=game-scraper/1.0
SCRAPER_REVIEW_MIN_LENGTH_CHARS=200
```

## Проверки и демо

```powershell
.\.venv\Scripts\python.exe -m pip install -r requirements-dev.txt
.\.venv\Scripts\pytest.exe -q
.\.venv\Scripts\ruff.exe check scraper tests
.\.venv\Scripts\mypy.exe scraper
```

В `demo/` находятся измерения Wikidata и примеры ORM-выгрузки.
Каталог AppID для демо по-прежнему берётся существующим Steam-путём.
