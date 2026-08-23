from __future__ import annotations

import pytest

from scraper.pipeline import (
    AppIdFileFeeder,
    InMemoryTaskQueue,
    PipelineServices,
    ScraperPipeline,
    TaskSubscriber,
)
from scraper.pipeline.models import (
    QUEUE_STEAM,
    QUEUE_WIKIDATA,
    wikidata_organization_name_task,
)
from scraper.sources.steam import SteamRefreshResult
from scraper.wikidata.sync import WikidataGameResult, WikidataOrganizationNameResult


@pytest.mark.asyncio
async def test_app_id_file_feeder_deduplicates_and_supports_test_limit(tmp_path) -> None:
    path = tmp_path / "appids.txt"
    path.write_text("# fixture\n620\n730\n620\n570\n", encoding="utf-8")
    queue = InMemoryTaskQueue([QUEUE_STEAM])

    added = await AppIdFileFeeder(path).seed(queue, limit=2)

    assert added == 2
    states = await queue.states()
    assert {state.task.app_id for state in states} == {620, 730}


@pytest.mark.asyncio
async def test_shared_organization_task_has_one_queue_entry_and_many_subscribers() -> None:
    queue = InMemoryTaskQueue([QUEUE_WIKIDATA])
    task = wikidata_organization_name_task(
        "Valve Corporation",
        app_id=620,
        relation="developer",
    )

    first = await queue.enqueue(
        task,
        subscriber=TaskSubscriber(app_id=620, relation="developer"),
    )
    second = await queue.enqueue(
        task,
        subscriber=TaskSubscriber(app_id=730, relation="developer"),
    )

    assert first.created is True
    assert second.created is False
    assert second.state.subscribers == {
        TaskSubscriber(app_id=620, relation="developer"),
        TaskSubscriber(app_id=730, relation="developer"),
    }
    assert len(await queue.states()) == 1


@pytest.mark.asyncio
async def test_pipeline_registers_wikidata_game_and_people_together() -> None:
    pipeline = ScraperPipeline(
        PipelineServices(
            steam=object(),  # type: ignore[arg-type]
            wikidata=object(),  # type: ignore[arg-type]
        )
    )

    await pipeline._enqueue_enrichment(
        620,
        SteamRefreshResult(
            refreshes=[],
            title="Portal 2",
            developers=["Valve Corporation"],
            publishers=["Valve Corporation"],
        ),
    )

    states = await pipeline.queue.states()
    keys = {state.task.task_key for state in states}
    assert "wikidata:game:app:620" in keys
    assert "wikidata:organization-name:valve corporation" in keys
    assert len(states) == 2
    organization = next(
        state for state in states if state.task.task_type == "organization_name"
    )
    assert organization.subscribers == {
        TaskSubscriber(app_id=620, relation="developer"),
        TaskSubscriber(app_id=620, relation="publisher"),
    }


@pytest.mark.asyncio
async def test_pipeline_runs_primary_then_independent_wikidata_tasks(tmp_path) -> None:
    events: list[str] = []

    class FakeSteam:
        async def refresh(self, app_id: int) -> SteamRefreshResult:
            events.append(f"steam:{app_id}")
            return SteamRefreshResult(
                refreshes=[],
                title="Example",
                developers=["Dev"],
                publishers=["Pub"],
            )

    class FakeWikidata:
        async def refresh_game_task(self, app_id: int) -> WikidataGameResult:
            events.append(f"wikidata-game:{app_id}")
            return WikidataGameResult(app_id, "QGAME", "ready", {"related": {"QREL"}})

        async def refresh_organization_name(self, name: str) -> WikidataOrganizationNameResult:
            events.append(f"wikidata-name:{name}")
            return WikidataOrganizationNameResult(name, [f"Q-{name}"])

        async def link_game_entity(self, app_id: int, qid: str, *, relation: str) -> None:
            events.append(f"link:{app_id}:{qid}:{relation}")

        async def refresh_entity(self, qid: str) -> None:
            events.append(f"wikidata-entity:{qid}")

    path = tmp_path / "mini-appids.txt"
    path.write_text("620\n", encoding="utf-8")
    pipeline = ScraperPipeline(
        PipelineServices(
            steam=FakeSteam(),  # type: ignore[arg-type]
            wikidata=FakeWikidata(),  # type: ignore[arg-type]
        )
    )

    assert await pipeline.run_from_file(str(path)) == 1
    assert events[0] == "steam:620"
    assert "wikidata-game:620" in events
    assert "wikidata-name:Dev" in events
    assert "wikidata-name:Pub" in events
    assert "link:620:QREL:related" in events
    assert "wikidata-entity:Q-Dev" in events
    assert "wikidata-entity:Q-Pub" in events
    assert "wikidata-entity:QREL" in events
