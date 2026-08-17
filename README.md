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

## Разработка

```powershell
.\.venv\Scripts\python.exe -m pip install -r requirements-dev.txt
.\.venv\Scripts\pytest.exe -q
.\.venv\Scripts\ruff.exe check scraper tests
```

Результат содержит локализации с plain text и исходным HTML, медиа-URL, требования
по ОС, Steam-категории и пользовательские теги, языковую матрицу, возрастные
рейтинги, достижения, рейтинги и выбранные отзывы. HLTB и Metacritic являются
best-effort источниками: при недоступности они оставляют поле пустым и добавляют
структурированный diagnostic.

`languages` управляет только языком текстов Steam. `store_country` управляет
региональным контекстом магазина и по умолчанию равен `kz` (Казахстан). Если
нужно не передавать региональный контекст Steam, можно явно указать
`store_country=None`. Автоматического перехода на другой регион нет.
