from __future__ import annotations

import argparse
import asyncio
import json
import math
import sys
import time
from collections import defaultdict
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any

import httpx
from sqlalchemy import func, select

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from scraper.pipeline import AppIdFileFeeder, PipelineServices, ScraperPipeline  # noqa: E402
from scraper.pipeline.models import (  # noqa: E402
    QUEUE_HLTB,
    QUEUE_METACRITIC,
    QUEUE_PCGAMINGWIKI,
    QUEUE_STEAM,
    QUEUE_STEAMSPY,
    QUEUE_WIKIDATA,
)
from scraper.sources import (  # noqa: E402
    HltbSyncService,
    MetacriticSyncService,
    PCGamingWikiSyncService,
    SteamGameSyncService,
    SteamSpySyncService,
)
from scraper.wikidata import (  # noqa: E402
    ScraperConfig,
    ScraperDatabase,
    SourceFact,
    SourceRefresh,
    WikidataGame,
    WikidataGameLink,
    WikidataSyncService,
)

DEFAULT_INPUT = PROJECT_ROOT / "appids.txt"
DEFAULT_OUTPUT_DIR = PROJECT_ROOT / "demo" / "new_run"
CHANNELS = (
    QUEUE_STEAM,
    QUEUE_WIKIDATA,
    QUEUE_STEAMSPY,
    QUEUE_PCGAMINGWIKI,
    QUEUE_HLTB,
    QUEUE_METACRITIC,
)


@dataclass(slots=True)
class HttpChannelMetrics:
    requests: int = 0
    successful: int = 0
    http_errors: int = 0
    exceptions: int = 0
    durations: list[float] | None = None

    def __post_init__(self) -> None:
        if self.durations is None:
            self.durations = []

    def add(self, duration: float, status_code: int | None, error: bool) -> None:
        self.requests += 1
        assert self.durations is not None
        self.durations.append(duration)
        if error:
            self.exceptions += 1
        elif status_code is not None and status_code >= 400:
            self.http_errors += 1
        else:
            self.successful += 1


def _channel_for_url(url: object) -> str:
    host = httpx.URL(str(url)).host or "unknown"
    host = host.casefold()
    if "steamspy" in host:
        return QUEUE_STEAMSPY
    if "steam" in host:
        return QUEUE_STEAM
    if "wikidata" in host or "wikimedia" in host:
        return QUEUE_WIKIDATA
    if "steamspy" in host:
        return QUEUE_STEAMSPY
    if "pcgamingwiki" in host:
        return QUEUE_PCGAMINGWIKI
    if "howlongtobeat" in host:
        return QUEUE_HLTB
    if "metacritic" in host:
        return QUEUE_METACRITIC
    return "unknown"


def _percentile(values: list[float]) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    index = min(len(ordered) - 1, max(0, math.ceil(len(ordered) * 0.95) - 1))
    return ordered[index]


def _timing_summary(values: list[float]) -> dict[str, float | int | None]:
    if not values:
        return {"count": 0, "total_seconds": 0.0, "avg_seconds": None, "p95_seconds": None}
    return {
        "count": len(values),
        "total_seconds": round(sum(values), 4),
        "avg_seconds": round(sum(values) / len(values), 4),
        "p95_seconds": round(_percentile(values) or 0.0, 4),
    }


def _metric_values(states: list[Any], attribute: str) -> list[float]:
    values: list[float] = []
    for state in states:
        value = getattr(state, attribute)
        if value is not None:
            values.append(float(value))
    return values


async def _http_metrics_run(
    pipeline: ScraperPipeline,
    input_path: Path,
    *,
    limit: int,
    metrics: dict[str, HttpChannelMetrics],
) -> int:
    original_get = httpx.AsyncClient.get

    async def counted_get(
        client: httpx.AsyncClient,
        url: Any,
        *args: Any,
        **kwargs: Any,
    ) -> httpx.Response:
        channel = _channel_for_url(url)
        channel_metrics = metrics.setdefault(channel, HttpChannelMetrics())
        started = time.perf_counter()
        try:
            response = await original_get(client, url, *args, **kwargs)
        except Exception:
            channel_metrics.add(time.perf_counter() - started, None, True)
            raise
        channel_metrics.add(time.perf_counter() - started, response.status_code, False)
        return response

    httpx.AsyncClient.get = counted_get  # type: ignore[assignment]
    try:
        return await pipeline.run_from_file(str(input_path), limit=limit)
    finally:
        httpx.AsyncClient.get = original_get  # type: ignore[method-assign]


def _fact_value(fact: SourceFact) -> object:
    values = {
        "text": fact.value_text,
        "int": fact.value_int,
        "float": fact.value_float,
        "bool": fact.value_bool,
    }
    return values.get(fact.value_type, fact.value_text)


def _is_scalar_valid(fact: SourceFact) -> bool:
    if fact.value_type in {"text", "decimal", "datetime"}:
        return (
            fact.value_text is not None
            and fact.value_int is None
            and fact.value_float is None
            and fact.value_bool is None
        )
    if fact.value_type == "int":
        return (
            fact.value_int is not None
            and fact.value_text is None
            and fact.value_float is None
            and fact.value_bool is None
        )
    if fact.value_type == "float":
        return (
            fact.value_float is not None
            and fact.value_text is None
            and fact.value_int is None
            and fact.value_bool is None
        )
    if fact.value_type == "bool":
        return (
            fact.value_bool is not None
            and fact.value_text is None
            and fact.value_int is None
            and fact.value_float is None
        )
    return False


def _normalize_name(name: str) -> str:
    return " ".join(name.casefold().split())


async def _validate_database(
    database: ScraperDatabase,
    app_ids: list[int],
    task_states: list[Any],
) -> dict[str, object]:
    state_keys = {(state.task.queue, state.task.task_key) for state in task_states}
    app_reports: list[dict[str, object]] = []
    invalid_facts: list[str] = []
    invalid_steamspy: list[str] = []
    missing_tasks: list[str] = []
    statuses_by_source: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))

    async with database.session() as session:
        all_facts = list((await session.scalars(select(SourceFact))).all())
        all_refreshes = list((await session.scalars(select(SourceRefresh))).all())
        all_games = {
            game.steam_app_id: game
            for game in (await session.scalars(select(WikidataGame))).all()
        }
        link_count_by_app: dict[int, int] = defaultdict(int)
        for link in (await session.scalars(select(WikidataGameLink))).all():
            link_count_by_app[link.steam_app_id] += 1

        duplicate_fact_groups = list(
            (
                await session.execute(
                    select(
                        SourceFact.source,
                        SourceFact.steam_app_id,
                        SourceFact.scope,
                        SourceFact.path,
                        func.count(SourceFact.id),
                    )
                    .group_by(
                        SourceFact.source,
                        SourceFact.steam_app_id,
                        SourceFact.scope,
                        SourceFact.path,
                    )
                    .having(func.count(SourceFact.id) > 1)
                )
            ).all()
        )
        duplicate_link_groups = list(
            (
                await session.execute(
                    select(
                        WikidataGameLink.steam_app_id,
                        WikidataGameLink.qid,
                        WikidataGameLink.relation,
                        func.count(WikidataGameLink.id),
                    )
                    .group_by(
                        WikidataGameLink.steam_app_id,
                        WikidataGameLink.qid,
                        WikidataGameLink.relation,
                    )
                    .having(func.count(WikidataGameLink.id) > 1)
                )
            ).all()
        )

    facts_by_app: dict[int, list[SourceFact]] = defaultdict(list)
    for fact in all_facts:
        facts_by_app[fact.steam_app_id].append(fact)
        if not _is_scalar_valid(fact):
            invalid_facts.append(
                f"{fact.source}/{fact.steam_app_id}/{fact.scope}/{fact.path}: invalid scalar"
            )
        statuses_by_source[fact.source]["facts"] += 1

    refreshes_by_app: dict[int, list[SourceRefresh]] = defaultdict(list)
    for refresh in all_refreshes:
        refreshes_by_app[refresh.steam_app_id].append(refresh)
        statuses_by_source[refresh.source][refresh.status] += 1

    for app_id in app_ids:
        facts = facts_by_app[app_id]
        app_refreshes = refreshes_by_app[app_id]
        source_scopes: dict[str, list[str]] = defaultdict(list)
        source_statuses: dict[str, dict[str, str]] = defaultdict(dict)
        for refresh in app_refreshes:
            source_scopes[refresh.source].append(refresh.scope)
            source_statuses[refresh.source][refresh.scope] = refresh.status

        steam_facts = [fact for fact in facts if fact.source == QUEUE_STEAM]
        steam_names = [
            str(_fact_value(fact))
            for fact in steam_facts
            if fact.path.startswith("developers[") or fact.path.startswith("publishers[")
        ]
        org_expectations: list[str] = []
        for fact in steam_facts:
            if not fact.path.startswith(("developers[", "publishers[")):
                continue
            name = _fact_value(fact)
            if not isinstance(name, str) or not name.strip():
                continue
            relation = "developer" if fact.path.startswith("developers[") else "publisher"
            task_key = f"wikidata:organization-name:{_normalize_name(name)}"
            org_expectations.append(task_key)
            if (QUEUE_WIKIDATA, task_key) not in state_keys:
                missing_tasks.append(f"{app_id}: missing {relation} organization task {name}")

        expected_tasks = [
            (QUEUE_STEAM, f"steam:game:{app_id}"),
            (QUEUE_WIKIDATA, f"wikidata:game:app:{app_id}"),
            (QUEUE_STEAMSPY, f"steamspy:game:{app_id}"),
            (QUEUE_PCGAMINGWIKI, f"pcgamingwiki:game:{app_id}"),
            (QUEUE_HLTB, f"hltb:game:{app_id}"),
            (QUEUE_METACRITIC, f"metacritic:game:{app_id}"),
        ]
        for queue, task_key in expected_tasks:
            if (queue, task_key) not in state_keys:
                missing_tasks.append(f"{app_id}: missing {queue} task")

        spy_facts = {
            fact.path: fact.value_int
            for fact in facts
            if fact.source == QUEUE_STEAMSPY and fact.value_type == "int"
        }
        owners_min = spy_facts.get("owners_min")
        owners_max = spy_facts.get("owners_max")
        if owners_min is not None and owners_min < 0:
            invalid_steamspy.append(f"{app_id}: owners_min < 0")
        if owners_max is not None and owners_max < 0:
            invalid_steamspy.append(f"{app_id}: owners_max < 0")
        if owners_min is not None and owners_max is not None and owners_min > owners_max:
            invalid_steamspy.append(f"{app_id}: owners_min > owners_max")
        for path, value in spy_facts.items():
            if (
                path in {
                    "average_forever_minutes",
                    "average_two_weeks_minutes",
                    "median_forever_minutes",
                    "median_two_weeks_minutes",
                    "ccu",
                }
                or path.startswith("tags.")
            ) and value is not None and value < 0:
                invalid_steamspy.append(f"{app_id}: {path} < 0")

        game = all_games.get(app_id)
        game_status = game.status if game is not None else "missing"
        if game_status == "not_found" and org_expectations:
            # This is the key missing-game fallback: organization tasks must remain present.
            unresolved = [
                key for key in org_expectations if (QUEUE_WIKIDATA, key) not in state_keys
            ]
            if unresolved:
                missing_tasks.extend(
                    f"{app_id}: missing-game fallback lost {key}" for key in unresolved
                )

        app_reports.append(
            {
                "app_id": app_id,
                "facts_by_source": {
                    source: sum(1 for fact in facts if fact.source == source)
                    for source in CHANNELS
                },
                "refresh_statuses": {
                    source: dict(statuses)
                    for source, statuses in sorted(source_statuses.items())
                },
                "steam_developers_publishers_seen": steam_names,
                "wikidata_game_status": game_status,
                "wikidata_item_qid": game.item_qid if game is not None else None,
                "wikidata_links": link_count_by_app.get(app_id, 0),
                "organization_tasks_expected": len(set(org_expectations)),
                "organization_tasks_present": sum(
                    (QUEUE_WIKIDATA, key) in state_keys for key in set(org_expectations)
                ),
                "steam_scopes": sorted(source_scopes.get(QUEUE_STEAM, [])),
            }
        )

    structural_errors = [
        *invalid_facts,
        *invalid_steamspy,
        *[f"duplicate SourceFact key: {row}" for row in duplicate_fact_groups],
        *[f"duplicate WikidataGameLink key: {row}" for row in duplicate_link_groups],
    ]
    source_failures = [
        f"{refresh.source}/{refresh.steam_app_id}/{refresh.scope}: {refresh.last_error or 'failed'}"
        for refresh in all_refreshes
        if refresh.status == "failed"
    ]
    source_not_found = [
        f"{refresh.source}/{refresh.steam_app_id}/{refresh.scope}"
        for refresh in all_refreshes
        if refresh.status == "not_found"
    ]
    return {
        "ok": not structural_errors and not missing_tasks and not source_failures,
        "structural_ok": not structural_errors,
        "task_completeness_ok": not missing_tasks,
        "source_execution_ok": not source_failures,
        "structural_errors": structural_errors,
        "missing_tasks": missing_tasks,
        "source_failures": source_failures,
        "source_not_found": source_not_found,
        "facts_total": len(all_facts),
        "refreshes_total": len(all_refreshes),
        "games_total": len(all_games),
        "app_reports": app_reports,
        "source_statuses": {
            source: dict(statuses) for source, statuses in sorted(statuses_by_source.items())
        },
    }


def _queue_metrics(
    states: list[Any],
    enqueue_metrics: dict[str, dict[str, int]],
) -> dict[str, object]:
    by_queue: dict[str, list[Any]] = defaultdict(list)
    by_type: dict[str, list[Any]] = defaultdict(list)
    for state in states:
        by_queue[state.task.queue].append(state)
        by_type[f"{state.task.queue}:{state.task.task_type}"].append(state)

    def summarize(group: list[Any]) -> dict[str, object]:
        waits = _metric_values(group, "wait_seconds")
        durations = _metric_values(group, "duration_seconds")
        return {
            "tasks": len(group),
            "done": sum(state.status == "done" for state in group),
            "failed": sum(state.status == "failed" for state in group),
            "pending_or_running": sum(state.status not in {"done", "failed"} for state in group),
            "wait": _timing_summary(waits),
            "duration": _timing_summary(durations),
        }

    queues = {}
    for queue in CHANNELS:
        queues[queue] = {
            **summarize(by_queue.get(queue, [])),
            "enqueue": enqueue_metrics.get(
                queue,
                {"attempted": 0, "created": 0, "deduplicated": 0},
            ),
        }
    return {
        "total_tasks": len(states),
        "total_done": sum(state.status == "done" for state in states),
        "total_failed": sum(state.status == "failed" for state in states),
        "queues": queues,
        "task_types": {task_type: summarize(group) for task_type, group in sorted(by_type.items())},
        "tasks": [
            {
                "queue": state.task.queue,
                "task_key": state.task.task_key,
                "task_type": state.task.task_type,
                "app_id": state.task.app_id,
                "entity_key": state.task.entity_key,
                "status": state.status,
                "wait_seconds": round(state.wait_seconds, 4)
                if state.wait_seconds is not None
                else None,
                "duration_seconds": round(state.duration_seconds, 4)
                if state.duration_seconds is not None
                else None,
                "subscribers": [
                    {"app_id": item.app_id, "relation": item.relation}
                    for item in sorted(
                        state.subscribers,
                        key=lambda item: (item.app_id, item.relation),
                    )
                ],
                "error": state.last_error,
            }
            for state in sorted(
                states,
                key=lambda item: (item.task.queue, item.task.task_key),
            )
        ],
    }


def _http_report(metrics: dict[str, HttpChannelMetrics]) -> dict[str, object]:
    report: dict[str, object] = {}
    for channel, channel_metrics in sorted(metrics.items()):
        durations = channel_metrics.durations or []
        report[channel] = {
            "requests": channel_metrics.requests,
            "successful": channel_metrics.successful,
            "http_errors": channel_metrics.http_errors,
            "exceptions": channel_metrics.exceptions,
            "timing": _timing_summary(durations),
        }
    return report


def _fmt_seconds(value: object) -> str:
    return f"{value:.3f} с" if isinstance(value, (int, float)) else "—"


def _render_report(benchmark: dict[str, object]) -> str:
    queue_metrics = benchmark["queue_metrics"]
    assert isinstance(queue_metrics, dict)
    queues = queue_metrics["queues"]
    assert isinstance(queues, dict)
    http_metrics = benchmark["http_metrics"]
    assert isinstance(http_metrics, dict)
    validation = benchmark["validation"]
    assert isinstance(validation, dict)
    app_reports = validation["app_reports"]
    assert isinstance(app_reports, list)
    app_ids = benchmark["app_ids"]
    assert isinstance(app_ids, list)
    elapsed_seconds = benchmark["elapsed_seconds"]
    assert isinstance(elapsed_seconds, (int, float))

    lines = [
        "# Отчет проверки единого пайплайна",
        "",
        f"- Дата запуска: `{benchmark['started_at_utc']}`",
        f"- Источник AppID: `{benchmark['input_file']}`",
        f"- Ограничение запуска: первые `{benchmark['limit']}` уникальных строк",
        f"- AppID в прогоне: `{', '.join(str(item) for item in app_ids)}`",
        f"- Общее время пайплайна: **{elapsed_seconds:.3f} с**",
        (
            f"- Задач: `{queue_metrics['total_tasks']}`, успешно: "
            f"`{queue_metrics['total_done']}`, с ошибкой: `{queue_metrics['total_failed']}`"
        ),
        "",
        "## Результат проверок",
        "",
        f"- Общий результат: **{'PASS' if validation['ok'] else 'CHECK REQUIRED'}**",
        f"- Структурная корректность ORM: **{'PASS' if validation['structural_ok'] else 'FAIL'}**",
        (
            f"- Полнота постановки задач: "
            f"**{'PASS' if validation['task_completeness_ok'] else 'FAIL'}**"
        ),
        (
            f"- Выполнение источников без статуса `failed`: "
            f"**{'PASS' if validation['source_execution_ok'] else 'CHECK REQUIRED'}**"
        ),
        "",
        (
            "Структурная проверка не утверждает, что внешний источник семантически прав. "
            "Она проверяет уникальность текущих фактов, допустимость скалярных значений, "
            "ограничения SteamSpy и наличие ожидаемых задач по цепочке."
        ),
        "",
        "## Метрики по каналам",
        "",
        (
            "| Канал | Задач | Done | Failed | Создано | Dedup | Среднее ожидание | "
            "P95 ожидания | Среднее выполнение | P95 выполнения | HTTP | HTTP avg | HTTP P95 |"
        ),
        "|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for channel in CHANNELS:
        queue = queues[channel]
        http = http_metrics.get(channel, {})
        assert isinstance(queue, dict)
        assert isinstance(http, dict)
        enqueue = queue["enqueue"]
        wait = queue["wait"]
        duration = queue["duration"]
        http_timing = http.get("timing", {})
        assert isinstance(enqueue, dict)
        assert isinstance(wait, dict)
        assert isinstance(duration, dict)
        assert isinstance(http_timing, dict)
        lines.append(
            (
                "| {channel} | {tasks} | {done} | {failed} | {created} | {dedup} | "
                "{wait_avg} | {wait_p95} | {duration_avg} | {duration_p95} | "
                "{requests} | {http_avg} | {http_p95} |"
            ).format(
                channel=channel,
                tasks=queue["tasks"],
                done=queue["done"],
                failed=queue["failed"],
                created=enqueue["created"],
                dedup=enqueue["deduplicated"],
                wait_avg=_fmt_seconds(wait["avg_seconds"]),
                wait_p95=_fmt_seconds(wait["p95_seconds"]),
                duration_avg=_fmt_seconds(duration["avg_seconds"]),
                duration_p95=_fmt_seconds(duration["p95_seconds"]),
                requests=http.get("requests", 0),
                http_avg=_fmt_seconds(http_timing.get("avg_seconds")),
                http_p95=_fmt_seconds(http_timing.get("p95_seconds")),
            )
        )

    lines.extend(
        [
            "",
            (
                "`Dedup` — число попыток постановки уже существовавших логических задач. "
                "HTTP-счетчик считает отдельные вызовы, включая повторные попытки клиента; "
                "HLTB может использовать внутренний клиент библиотеки, поэтому его "
                "HTTP-колонка может быть неполной, а время задачи — полным."
            ),
            "",
            "## Покрытие по играм",
            "",
            (
                "| AppID | Steam facts | Wikidata game | Wikidata QID | Wikidata links | "
                "Steam developers/publishers | Org tasks |"
            ),
            "|---:|---:|---|---|---:|---|---:|",
        ]
    )

    for app in app_reports:
        assert isinstance(app, dict)
        facts = app["facts_by_source"]
        assert isinstance(facts, dict)
        lines.append(
            f"| {app['app_id']} | {facts.get(QUEUE_STEAM, 0)} | "
            f"{app['wikidata_game_status']} | {app['wikidata_item_qid'] or '—'} | "
            f"{app['wikidata_links']} | "
            f"{', '.join(app['steam_developers_publishers_seen']) or '—'} | "
            f"{app['organization_tasks_present']}/{app['organization_tasks_expected']} |"
        )

    lines.extend(
        [
            "",
            "## Статусы источников",
            "",
            "| Источник | Ready | Not found | Failed | Фактов |",
            "|---|---:|---:|---:|---:|",
        ]
    )
    source_statuses = validation["source_statuses"]
    assert isinstance(source_statuses, dict)
    for source, statuses in sorted(source_statuses.items()):
        assert isinstance(statuses, dict)
        lines.append(
            f"| {source} | {statuses.get('ready', 0)} | {statuses.get('not_found', 0)} | "
            f"{statuses.get('failed', 0)} | {statuses.get('facts', 0)} |"
        )

    structural_errors = validation["structural_errors"]
    missing_tasks = validation["missing_tasks"]
    source_failures = validation["source_failures"]
    source_not_found = validation["source_not_found"]
    assert isinstance(structural_errors, list)
    assert isinstance(missing_tasks, list)
    assert isinstance(source_failures, list)
    assert isinstance(source_not_found, list)
    lines.extend(["", "## Проблемы", ""])
    if not structural_errors and not missing_tasks and not source_failures:
        lines.append(
            "Проблем структурной целостности, полноты очередей и выполнения источников "
            "не обнаружено."
        )
    else:
        for error in structural_errors:
            lines.append(f"- ORM: {error}")
        for error in missing_tasks:
            lines.append(f"- Очередь: {error}")
        for error in source_failures:
            lines.append(f"- Источник: {error}")
    if source_not_found:
        lines.extend(
            [
                "",
                "Допустимые результаты `not_found` "
                "(источник ответил, но совпадение не найдено):",
            ]
        )
        lines.extend(f"- {item}" for item in source_not_found)

    lines.extend(
        [
            "",
            "## Ограничения интерпретации",
            "",
            (
                "- Статусы `not_found` и ошибки внешних источников отражают результат "
                "конкретного запуска и не являются автоматически ошибкой ORM."
            ),
            (
                "- Времена зависят от сети, состояния внешних API, TTL и текущего лимита "
                "запросов; их следует сравнивать между одинаковыми конфигурациями."
            ),
            (
                "- Очередь в этом прогоне in-memory. В SQLite сохраняются только текущие "
                "факты, состояния TTL и результаты источников; история запусков не записывается."
            ),
        ]
    )
    return "\n".join(lines) + "\n"


async def run(
    input_path: Path = DEFAULT_INPUT,
    output_dir: Path = DEFAULT_OUTPUT_DIR,
    *,
    limit: int = 5,
) -> dict[str, object]:
    if limit < 1:
        raise ValueError("limit must be positive")
    await asyncio.to_thread(output_dir.mkdir, parents=True, exist_ok=True)
    database_path = await asyncio.to_thread(lambda: (output_dir / "pipeline.sqlite3").resolve())
    benchmark_path = output_dir / "pipeline_benchmark.json"
    report_path = output_dir / "pipeline_report.md"
    database_path.unlink(missing_ok=True)
    input_path_resolved = await asyncio.to_thread(input_path.resolve)

    config = replace(
        ScraperConfig.from_env(),
        database_url=f"sqlite+aiosqlite:///{database_path.as_posix()}",
        app_ids_file=str(input_path_resolved),
    )
    app_ids = AppIdFileFeeder(input_path).read(limit=limit)
    if len(app_ids) != limit:
        raise ValueError(f"Expected {limit} AppIDs, found {len(app_ids)}")

    database = ScraperDatabase(config)
    pipeline = ScraperPipeline(
        PipelineServices(
            steam=SteamGameSyncService(database),
            wikidata=WikidataSyncService(database),
            steamspy=SteamSpySyncService(database),
            pcgamingwiki=PCGamingWikiSyncService(database),
            hltb=HltbSyncService(database),
            metacritic=MetacriticSyncService(database),
        )
    )
    http_metrics: dict[str, HttpChannelMetrics] = {}
    started_at = time.perf_counter()
    started_at_utc = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    try:
        added = await _http_metrics_run(
            pipeline,
            input_path,
            limit=limit,
            metrics=http_metrics,
        )
        states = await pipeline.queue.states()
        enqueue_metrics = await pipeline.queue.enqueue_metrics()
        validation = await _validate_database(database, app_ids, states)
    finally:
        await database.dispose()
    elapsed_seconds = time.perf_counter() - started_at

    benchmark: dict[str, object] = {
        "started_at_utc": started_at_utc,
        "input_file": str(input_path_resolved),
        "limit": limit,
        "app_ids": app_ids,
        "database_file": str(database_path),
        "added_primary_tasks": added,
        "elapsed_seconds": round(elapsed_seconds, 4),
        "queue_metrics": _queue_metrics(states, enqueue_metrics),
        "http_metrics": _http_report(http_metrics),
        "validation": validation,
    }
    benchmark_path.write_text(
        json.dumps(benchmark, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    report_path.write_text(_render_report(benchmark), encoding="utf-8")
    return benchmark


def main() -> None:
    parser = argparse.ArgumentParser(description="Run and measure the six-channel scraper pipeline")
    parser.add_argument("--input", type=Path, default=DEFAULT_INPUT)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    parser.add_argument("--limit", type=int, default=5)
    args = parser.parse_args()
    benchmark = asyncio.run(run(args.input, args.output_dir, limit=args.limit))
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    print(json.dumps(benchmark, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
