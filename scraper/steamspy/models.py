from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field


class SteamSpyModel(BaseModel):
    model_config = ConfigDict(extra="ignore")


class SteamSpyStats(SteamSpyModel):
    """SteamSpy appdetails metrics; persisted by SteamSpySyncService."""

    app_id: int
    owners_min: int | None = None
    owners_max: int | None = None
    average_forever_minutes: int | None = None
    average_two_weeks_minutes: int | None = None
    median_forever_minutes: int | None = None
    median_two_weeks_minutes: int | None = None
    ccu: int | None = None
    tags: dict[str, int] = Field(default_factory=dict)


__all__ = ["SteamSpyStats"]
