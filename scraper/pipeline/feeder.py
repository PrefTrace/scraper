from __future__ import annotations

from pathlib import Path

from .models import steam_game_task
from .queue import InMemoryTaskQueue


class AppIdFileFeeder:
    """Seed Steam tasks from the configured current AppID file."""

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)

    def read(self, *, limit: int | None = None) -> list[int]:
        if limit is not None and limit < 0:
            raise ValueError("limit cannot be negative")
        if limit == 0:
            return []
        app_ids: list[int] = []
        seen: set[int] = set()
        for raw_line in self.path.read_text(encoding="utf-8").splitlines():
            line = raw_line.strip()
            if not line or line.startswith("#"):
                continue
            try:
                app_id = int(line)
            except ValueError as exc:
                raise ValueError(f"Invalid Steam AppID in {self.path}: {line!r}") from exc
            if app_id < 0:
                raise ValueError(f"Steam AppID cannot be negative: {app_id}")
            if app_id not in seen:
                seen.add(app_id)
                app_ids.append(app_id)
                if limit is not None and len(app_ids) >= limit:
                    break
        return app_ids

    async def seed(
        self,
        queue: InMemoryTaskQueue,
        *,
        limit: int | None = None,
    ) -> int:
        added = 0
        for app_id in self.read(limit=limit):
            result = await queue.enqueue(steam_game_task(app_id))
            if result.created:
                added += 1
        return added


__all__ = ["AppIdFileFeeder"]
