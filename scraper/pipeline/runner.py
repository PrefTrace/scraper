from __future__ import annotations

from dataclasses import dataclass

from scraper.sources.hltb import HltbSyncService
from scraper.sources.metacritic import MetacriticSyncService
from scraper.sources.pcgamingwiki import PCGamingWikiSyncService
from scraper.sources.steam import SteamGameSyncService, SteamRefreshResult
from scraper.sources.steamspy import SteamSpySyncService
from scraper.wikidata.sync import (
    WikidataGameResult,
    WikidataOrganizationNameResult,
    WikidataSyncService,
)

from .feeder import AppIdFileFeeder
from .models import (
    QUEUE_HLTB,
    QUEUE_METACRITIC,
    QUEUE_PCGAMINGWIKI,
    QUEUE_STEAM,
    QUEUE_STEAMSPY,
    QUEUE_WIKIDATA,
    QueueTask,
    TaskSubscriber,
    source_game_task,
    wikidata_entity_task,
    wikidata_game_task,
    wikidata_organization_name_task,
)
from .queue import InMemoryTaskQueue


@dataclass(slots=True)
class PipelineServices:
    steam: SteamGameSyncService
    wikidata: WikidataSyncService
    steamspy: SteamSpySyncService | None = None
    pcgamingwiki: PCGamingWikiSyncService | None = None
    hltb: HltbSyncService | None = None
    metacritic: MetacriticSyncService | None = None


class ScraperPipeline:
    """Orchestrate source queues; source services never create child tasks."""

    def __init__(self, services: PipelineServices) -> None:
        self.services = services
        queue_names = [QUEUE_STEAM, QUEUE_WIKIDATA]
        if services.steamspy is not None:
            queue_names.append(QUEUE_STEAMSPY)
        if services.pcgamingwiki is not None:
            queue_names.append(QUEUE_PCGAMINGWIKI)
        if services.hltb is not None:
            queue_names.append(QUEUE_HLTB)
        if services.metacritic is not None:
            queue_names.append(QUEUE_METACRITIC)
        self.queue = InMemoryTaskQueue(queue_names)

    async def start(self) -> None:
        await self.queue.start_worker(QUEUE_STEAM, self._handle_steam)
        await self.queue.start_worker(QUEUE_WIKIDATA, self._handle_wikidata)
        handlers = (
            (QUEUE_STEAMSPY, self._handle_steamspy),
            (QUEUE_PCGAMINGWIKI, self._handle_pcgamingwiki),
            (QUEUE_HLTB, self._handle_hltb),
            (QUEUE_METACRITIC, self._handle_metacritic),
        )
        for queue_name, handler in handlers:
            if self.queue.has_queue(queue_name):
                await self.queue.start_worker(queue_name, handler)

    async def run_from_file(self, path: str, *, limit: int | None = None) -> int:
        feeder = AppIdFileFeeder(path)
        await self.start()
        try:
            added = await feeder.seed(self.queue, limit=limit)
            await self.queue.wait_idle()
            return added
        finally:
            await self.queue.stop()

    async def _handle_steam(self, task: QueueTask, _queue: InMemoryTaskQueue) -> object:
        assert task.app_id is not None
        result = await self.services.steam.refresh(task.app_id)
        await self._enqueue_enrichment(task.app_id, result)
        return result

    async def _enqueue_enrichment(self, app_id: int, result: SteamRefreshResult) -> None:
        await self.queue.enqueue(wikidata_game_task(app_id))
        for name in result.developers:
            await self._enqueue_organization_name(
                name,
                app_id=app_id,
                relation="developer",
            )
        for name in result.publishers:
            await self._enqueue_organization_name(
                name,
                app_id=app_id,
                relation="publisher",
            )
        if self.services.steamspy is not None:
            await self.queue.enqueue(source_game_task(QUEUE_STEAMSPY, "steamspy", app_id))
        if self.services.pcgamingwiki is not None:
            await self.queue.enqueue(
                source_game_task(QUEUE_PCGAMINGWIKI, "pcgamingwiki", app_id, title=result.title)
            )
        if result.title and self.services.hltb is not None:
            await self.queue.enqueue(
                source_game_task(QUEUE_HLTB, "hltb", app_id, title=result.title)
            )
        if result.title and self.services.metacritic is not None:
            await self.queue.enqueue(
                source_game_task(QUEUE_METACRITIC, "metacritic", app_id, title=result.title)
            )

    async def _enqueue_organization_name(
        self,
        name: str,
        *,
        app_id: int,
        relation: str,
    ) -> None:
        task = wikidata_organization_name_task(
            name,
            app_id=app_id,
            relation=relation,
        )
        registration = await self.queue.enqueue(
            task,
            subscriber=TaskSubscriber(app_id=app_id, relation=relation),
        )
        if (
            not registration.created
            and registration.state.status == "done"
            and isinstance(registration.state.result, WikidataOrganizationNameResult)
        ):
            await self._link_organization_result(
                registration.state.result,
                registration.state.subscribers,
            )

    async def _link_organization_result(
        self,
        result: WikidataOrganizationNameResult,
        subscribers: set[TaskSubscriber],
    ) -> None:
        for subscriber in subscribers:
            for qid in result.qids:
                await self.services.wikidata.link_game_entity(
                    subscriber.app_id,
                    qid,
                    relation=subscriber.relation,
                )
                await self.queue.enqueue(wikidata_entity_task(qid))

    async def _handle_wikidata(self, task: QueueTask, queue: InMemoryTaskQueue) -> object:
        if task.task_type == "game":
            assert task.app_id is not None
            result = await self.services.wikidata.refresh_game_task(task.app_id)
            assert isinstance(result, WikidataGameResult)
            for relation, qids in result.links.items():
                for qid in qids:
                    await self.services.wikidata.link_game_entity(
                        task.app_id,
                        qid,
                        relation=relation,
                    )
                    await queue.enqueue(wikidata_entity_task(qid))
            return result
        if task.task_type == "organization_name":
            assert task.app_id is not None and task.name is not None
            name_result = await self.services.wikidata.refresh_organization_name(task.name)
            assert isinstance(name_result, WikidataOrganizationNameResult)
            state = await queue.get_state(task.queue, task.task_key)
            subscribers = state.subscribers if state is not None else set()
            await self._link_organization_result(name_result, subscribers)
            return name_result
        if task.task_type == "entity":
            assert task.entity_key is not None
            return await self.services.wikidata.refresh_entity(task.entity_key)
        raise ValueError(f"Unsupported Wikidata task type: {task.task_type}")

    async def _handle_steamspy(self, task: QueueTask, _queue: InMemoryTaskQueue) -> object:
        assert task.app_id is not None and self.services.steamspy is not None
        return await self.services.steamspy.refresh(task.app_id)

    async def _handle_pcgamingwiki(self, task: QueueTask, _queue: InMemoryTaskQueue) -> object:
        assert task.app_id is not None and self.services.pcgamingwiki is not None
        return await self.services.pcgamingwiki.refresh(task.app_id, title=task.name)

    async def _handle_hltb(self, task: QueueTask, _queue: InMemoryTaskQueue) -> object:
        assert task.app_id is not None and task.name is not None and self.services.hltb is not None
        return await self.services.hltb.refresh(task.app_id, task.name)

    async def _handle_metacritic(self, task: QueueTask, _queue: InMemoryTaskQueue) -> object:
        assert task.app_id is not None and task.name is not None
        assert self.services.metacritic is not None
        return await self.services.metacritic.refresh(task.app_id, title=task.name)


__all__ = ["PipelineServices", "ScraperPipeline"]
