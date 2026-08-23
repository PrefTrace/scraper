# Steam data for Metabase

Основной артефакт: `steam_metabase.sqlite`.

Подключение в self-hosted Metabase: **Admin → Databases → Add a database → SQLite**, затем указать абсолютный путь к файлу. SQLite поддерживается официальным драйвером Metabase; для Metabase Cloud нужен PostgreSQL или другой серверный источник.

## Что внутри

- `dim_game` — одна строка на игру, актуальные стабильные атрибуты;
- `game_snapshot` — три снимка каталога: май 2024, март 2025, август 2026;
- `game_genre`, `game_tag`, `game_developer`, `game_publisher`, `game_category` — нормализованные связи many-to-many;
- `review_game`, `review_game_month` — агрегаты архива `weighted_score_above_08.csv.zip`;
- `recommendation_game`, `recommendation_game_month` — агрегаты 41 154 794 рекомендаций;
- `game_snapshot_comparison` — сравнение AppID между снимками;
- `market_year_summary` — сводка по году релиза и снимку;
- `join_coverage` — контроль покрытия объединения фактов с каталогом;
- `source_manifest`, `data_quality_check` — происхождение и результаты аудита.

## Важная очистка

В исходном `games.csv` было 39 полей в заголовке и 40 полей в строках. Заголовок `DiscountDLC count` разделён на `Discount` и `DLC count` только в нормализованном слое. Исходник не изменялся.

В текущем CSV списки жанров, тегов, категорий, разработчиков и издателей хранятся через запятую; в старых снимках они записаны как Python-подобные списки/словари. Все варианты приведены к отдельным bridge-таблицам.

Агрегаты отзывов построены только по файлу с фильтром `weighted_score_above_08`; это не полный корпус отзывов. Сырые пользовательские ID в SQLite не записываются.
