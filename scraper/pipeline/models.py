from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime

QUEUE_STEAM = "steam"
QUEUE_WIKIDATA = "wikidata"
QUEUE_STEAMSPY = "steamspy"
QUEUE_PCGAMINGWIKI = "pcgamingwiki"
QUEUE_HLTB = "hltb"
QUEUE_METACRITIC = "metacritic"


@dataclass(frozen=True, slots=True)
class TaskSubscriber:
    """A game that consumes the result of a shared logical task."""

    app_id: int
    relation: str


@dataclass(frozen=True, slots=True)
class QueueTask:
    """One independently executable source operation."""

    queue: str
    task_key: str
    task_type: str
    app_id: int | None = None
    entity_key: str | None = None
    scope: str | None = None
    name: str | None = None
    relation: str | None = None


@dataclass(slots=True)
class TaskState:
    task: QueueTask
    status: str = "pending"
    last_error: str | None = None
    result: object | None = None
    enqueued_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    started_at: datetime | None = None
    completed_at: datetime | None = None
    subscribers: set[TaskSubscriber] = field(default_factory=set)

    @property
    def wait_seconds(self) -> float | None:
        if self.started_at is None:
            return None
        return (self.started_at - self.enqueued_at).total_seconds()

    @property
    def duration_seconds(self) -> float | None:
        if self.started_at is None or self.completed_at is None:
            return None
        return (self.completed_at - self.started_at).total_seconds()


@dataclass(frozen=True, slots=True)
class EnqueueResult:
    task: QueueTask
    created: bool
    state: TaskState


def steam_game_task(app_id: int) -> QueueTask:
    return QueueTask(
        queue=QUEUE_STEAM,
        task_key=f"steam:game:{app_id}",
        task_type="game",
        app_id=app_id,
        scope="details",
    )


def wikidata_game_task(app_id: int) -> QueueTask:
    return QueueTask(
        queue=QUEUE_WIKIDATA,
        task_key=f"wikidata:game:app:{app_id}",
        task_type="game",
        app_id=app_id,
        entity_key=f"app:{app_id}",
        scope="game",
    )


def wikidata_organization_name_task(
    name: str,
    *,
    app_id: int,
    relation: str,
) -> QueueTask:
    normalized = " ".join(name.casefold().split())
    return QueueTask(
        queue=QUEUE_WIKIDATA,
        task_key=f"wikidata:organization-name:{normalized}",
        task_type="organization_name",
        app_id=app_id,
        entity_key=f"name:{normalized}",
        scope="organization_search",
        name=name.strip(),
        relation=relation,
    )


def wikidata_entity_task(qid: str) -> QueueTask:
    return QueueTask(
        queue=QUEUE_WIKIDATA,
        task_key=f"wikidata:entity:{qid}:full",
        task_type="entity",
        entity_key=qid,
        scope="full",
    )


def source_game_task(
    queue: str,
    source: str,
    app_id: int,
    *,
    title: str | None = None,
) -> QueueTask:
    return QueueTask(
        queue=queue,
        task_key=f"{source}:game:{app_id}",
        task_type="game",
        app_id=app_id,
        scope="game",
        name=title,
    )


__all__ = [
    "EnqueueResult",
    "QUEUE_HLTB",
    "QUEUE_METACRITIC",
    "QUEUE_PCGAMINGWIKI",
    "QUEUE_STEAM",
    "QUEUE_STEAMSPY",
    "QUEUE_WIKIDATA",
    "QueueTask",
    "TaskState",
    "TaskSubscriber",
    "source_game_task",
    "steam_game_task",
    "wikidata_entity_task",
    "wikidata_game_task",
    "wikidata_organization_name_task",
]
