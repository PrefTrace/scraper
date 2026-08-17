# Steam game data scraper

Единое асинхронное приложение для сбора нормализованных данных со страницы игры в
Steam. Внутренние подпакеты `steam`, `hltb` и `metacritic` являются частью одного
приложения и отдельно не устанавливаются и не версионируются.

## Быстрый старт

```powershell
py -3.14 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
```

```python
import asyncio

from scraper import scrape


async def main() -> None:
    game = await scrape(
        "https://store.steampowered.com/app/620/Portal_2/",
        languages=["ru-RU", "en-US"],
        store_country="kz",
        positive_review_count=4,
        negative_review_count=4,
        review_pages=1,
    )
    print(game.model_dump_json(indent=2))


asyncio.run(main())
```

## Получение каталога AppID

Для получения только идентификаторов приложений используется официальный
`IStoreService/GetAppList`; карточки игр и сопроводительные данные не загружаются:

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

Метод проходит все страницы официального каталога, удаляет дубликаты и
возвращает отсортированный `list[int]`. Для проверки можно ограничить результат
параметром `max_app_ids`; по умолчанию собирается максимально полный каталог.

## Разработка

```powershell
.\.venv\Scripts\python.exe -m pip install -r requirements-dev.txt
.\.venv\Scripts\pytest.exe -q
.\.venv\Scripts\ruff.exe check scraper tests
```

Результат содержит локализации с plain text и исходным HTML, медиа-URL, цену и
валюту выбранного региона, требования по ОС, Steam-категории и пользовательские
теги, языковую матрицу, возрастные рейтинги, достижения, рейтинги и выбранные
отзывы. HLTB и Metacritic являются
best-effort источниками: при недоступности они оставляют поле пустым и добавляют
структурированный diagnostic.

`languages` управляет только языком текстов Steam. `store_country` управляет
региональным контекстом магазина и по умолчанию равен `kz` (Казахстан). Если
нужно не передавать региональный контекст Steam, можно явно указать
`store_country=None`. Автоматического перехода на другой регион нет.
