from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

type CargoScalar = str | int | float | bool | list[str] | None


class PCGamingWikiModel(BaseModel):
    model_config = ConfigDict(extra="ignore")


class PCGamingWikiCargoField(PCGamingWikiModel):
    """Schema metadata returned by the Cargo ``cargofields`` action."""

    name: str
    field_type: str
    is_list: bool = False
    delimiter: str = ","


class PCGamingWikiCargoRow(PCGamingWikiModel):
    """One current row from one PCGamingWiki Cargo table."""

    table: str
    page_id: int | None = None
    page_title: str | None = None
    values: dict[str, CargoScalar] = Field(default_factory=dict)


class PCGamingWikiGame(PCGamingWikiModel):
    """A game page represented exclusively by current Cargo rows."""

    steam_app_id: int
    page_title: str
    page_url: str
    page_id: int
    cargo_rows: list[PCGamingWikiCargoRow] = Field(default_factory=list)


__all__ = [
    "CargoScalar",
    "PCGamingWikiCargoField",
    "PCGamingWikiCargoRow",
    "PCGamingWikiGame",
]
