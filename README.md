# Steam game data scraper

Асинхронный набор source-сервисов. Сервисы не возвращают агрегированный JSON:
они получают AppID, проверяют TTL в SQLite и сохраняют обновлённые значения в
операционные таблицы `SourceRefresh`/`SourceDiagnostic` и source-specific ORM-
таблицы соответствующего источника. Steam хранится в таблицах `steam_*`.
Wikidata сейчас deprecated и в активный pipeline не подключён; его legacy-код
не является частью текущего запуска.

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
        # Video and hardware AppIDs are outside the Steam-game TZ scope.
        include_videos=False,
        include_hardware=False,
    )
    print(len(app_ids), app_ids[:10])


asyncio.run(main())
```

## Pipeline и очереди

В первой версии feeder читает AppID из файла, указанного в
`SCRAPER_APPIDS_FILE`. Очередь Steam находится в памяти процесса. Для
активного источника запускается один асинхронный worker.

```python
import asyncio

from scraper import (
    PipelineServices,
    ScraperConfig,
    ScraperDatabase,
    ScraperPipeline,
    SteamGameSyncService,
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
                wikidata=None,  # deprecated; не запускается
            )
        )
        # Для тестового запуска используется ограниченный префикс файла.
        await pipeline.run_from_file(config.app_ids_file, limit=10)
    finally:
        await database.dispose()


asyncio.run(main())
```

Очередь хранит только текущие задачи в памяти; TTL и результаты Steam
сохраняются в ORM. Wikidata оставлен вне текущего pipeline до отдельной задачи.

## Wikidata (deprecated)

Wikidata не ставится в очередь, не запускает worker и не участвует в Steam-only
схеме или бенчмарке. Legacy-пакет переименован в `scraper/wikidata_deprecated`
и сохранён для последующего отдельного решения.

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

В Steam-specific mapping для credits намеренно хранится `organization_name`:
публичный Steam primary source не даёт стабильного `organization_id`, поэтому
фиктивный ID не создаётся. Разрешение организации в общую entity относится к
отдельному merge-stage.

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
STEAM_WEB_API_KEY=...
```

`STEAM_WEB_API_KEY` нужен только для structured achievements и global
achievement percentages; без него achievement scope получает явный diagnostic
и не считается покрытым.

## Проверки и демо

```powershell
.\.venv\Scripts\python.exe -m pip install -r requirements-dev.txt
.\.venv\Scripts\pytest.exe -q
.\.venv\Scripts\ruff.exe check scraper tests
.\.venv\Scripts\mypy.exe scraper
```

Для реального прогона Steam используй `demo/benchmark_steam.py`; исторический
`demo/benchmark_wikidata.py` не относится к текущему pipeline. Каталог AppID
для демо берётся существующим Steam-путём.
