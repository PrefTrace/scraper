from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field


class PCGamingWikiModel(BaseModel):
    model_config = ConfigDict(extra="ignore")


class PCGamingWikiGame(PCGamingWikiModel):
    """Parsed PCGamingWiki page; persistence is performed by the source service."""

    steam_app_id: int
    page_title: str
    page_url: str
    page_id: int | None = None
    steam_app_ids: list[int] = Field(default_factory=list)
    cover_url: str | None = None
    developers: list[str] = Field(default_factory=list)
    publishers: list[str] = Field(default_factory=list)
    engines: list[str] = Field(default_factory=list)
    releases: list[str] = Field(default_factory=list)
    external_ids: dict[str, str] = Field(default_factory=dict)
    sections: dict[str, str] = Field(default_factory=dict)
    infobox: dict[str, str] = Field(default_factory=dict)


__all__ = ["PCGamingWikiGame"]
