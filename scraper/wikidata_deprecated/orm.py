from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import (
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship

from .config import ScraperConfig


def utcnow() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


class Base(DeclarativeBase):
    pass


class WikidataEntity(Base):
    __tablename__ = "wikidata_entities"

    qid: Mapped[str] = mapped_column(String(32), primary_key=True)
    entity_kind: Mapped[str] = mapped_column(String(16), default="item")
    entity_type: Mapped[str] = mapped_column(String(32), default="unknown")
    label: Mapped[str | None] = mapped_column(Text, nullable=True)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    datatype: Mapped[str | None] = mapped_column(String(32), nullable=True)
    formatter_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    labels_fetched_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    full_fetched_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    source_revision: Mapped[int | None] = mapped_column(Integer, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)

    aliases: Mapped[list[WikidataAlias]] = relationship(
        back_populates="entity",
        cascade="all, delete-orphan",
    )
    facts: Mapped[list[WikidataFact]] = relationship(
        back_populates="subject",
        cascade="all, delete-orphan",
    )


class WikidataAlias(Base):
    __tablename__ = "wikidata_aliases"

    qid: Mapped[str] = mapped_column(
        ForeignKey("wikidata_entities.qid", ondelete="CASCADE"),
        primary_key=True,
    )
    alias: Mapped[str] = mapped_column(Text, primary_key=True)
    language: Mapped[str] = mapped_column(String(16), primary_key=True)

    entity: Mapped[WikidataEntity] = relationship(back_populates="aliases")


class WikidataFact(Base):
    __tablename__ = "wikidata_facts"
    __table_args__ = (Index("ix_wikidata_facts_subject_property", "subject_qid", "property_id"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    subject_qid: Mapped[str] = mapped_column(
        ForeignKey("wikidata_entities.qid", ondelete="CASCADE"),
        index=True,
    )
    property_id: Mapped[str] = mapped_column(String(16), index=True)
    value_type: Mapped[str] = mapped_column(String(32))
    value_qid: Mapped[str | None] = mapped_column(String(32), nullable=True, index=True)
    value_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    value_amount: Mapped[str | None] = mapped_column(String(128), nullable=True)
    value_unit: Mapped[str | None] = mapped_column(String(64), nullable=True)
    value_time: Mapped[str | None] = mapped_column(String(64), nullable=True)
    value_precision: Mapped[int | None] = mapped_column(Integer, nullable=True)
    value_calendar_model: Mapped[str | None] = mapped_column(String(32), nullable=True)
    rank: Mapped[str] = mapped_column(String(16), default="normal")
    observed_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    source_revision: Mapped[int | None] = mapped_column(Integer, nullable=True)

    subject: Mapped[WikidataEntity] = relationship(back_populates="facts")
    qualifiers: Mapped[list[WikidataQualifier]] = relationship(
        back_populates="fact",
        cascade="all, delete-orphan",
    )


class WikidataQualifier(Base):
    __tablename__ = "wikidata_qualifiers"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    fact_id: Mapped[int] = mapped_column(
        ForeignKey("wikidata_facts.id", ondelete="CASCADE"),
        index=True,
    )
    property_id: Mapped[str] = mapped_column(String(16), index=True)
    value_type: Mapped[str] = mapped_column(String(32))
    value_qid: Mapped[str | None] = mapped_column(String(32), nullable=True, index=True)
    value_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    value_amount: Mapped[str | None] = mapped_column(String(128), nullable=True)
    value_unit: Mapped[str | None] = mapped_column(String(64), nullable=True)
    value_time: Mapped[str | None] = mapped_column(String(64), nullable=True)
    value_precision: Mapped[int | None] = mapped_column(Integer, nullable=True)
    value_calendar_model: Mapped[str | None] = mapped_column(String(32), nullable=True)

    fact: Mapped[WikidataFact] = relationship(back_populates="qualifiers")


class WikidataNameLookup(Base):
    """Current TTL state for resolving a Steam organization name."""

    __tablename__ = "wikidata_name_lookups"

    normalized_name: Mapped[str] = mapped_column(String(256), primary_key=True)
    searched_name: Mapped[str] = mapped_column(Text)
    searched_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class WikidataNameLookupResult(Base):
    """Current Wikidata candidates for one normalized organization name."""

    __tablename__ = "wikidata_name_lookup_results"
    __table_args__ = (
        UniqueConstraint(
            "normalized_name",
            "qid",
            name="uq_wikidata_name_lookup_result",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    normalized_name: Mapped[str] = mapped_column(
        ForeignKey("wikidata_name_lookups.normalized_name", ondelete="CASCADE"),
        index=True,
    )
    qid: Mapped[str] = mapped_column(
        String(32),
        index=True,
    )


class WikidataGame(Base):
    __tablename__ = "wikidata_games"

    steam_app_id: Mapped[int] = mapped_column(Integer, primary_key=True)
    item_qid: Mapped[str | None] = mapped_column(
        ForeignKey("wikidata_entities.qid"),
        index=True,
        nullable=True,
    )
    lookup_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    refreshed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    status: Mapped[str] = mapped_column(String(16), default="ready")
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)


class WikidataGameLink(Base):
    __tablename__ = "wikidata_game_links"
    __table_args__ = (
        UniqueConstraint("steam_app_id", "qid", "relation", name="uq_wikidata_game_link"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    steam_app_id: Mapped[int] = mapped_column(
        ForeignKey("wikidata_games.steam_app_id", ondelete="CASCADE"),
        index=True,
    )
    qid: Mapped[str] = mapped_column(
        ForeignKey("wikidata_entities.qid", ondelete="CASCADE"),
        index=True,
    )
    relation: Mapped[str] = mapped_column(String(32), index=True)
    observed_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class SourceRefresh(Base):
    """Freshness and status of one source substructure for one Steam game."""

    __tablename__ = "source_refreshes"

    source: Mapped[str] = mapped_column(String(32), primary_key=True)
    steam_app_id: Mapped[int] = mapped_column(Integer, primary_key=True)
    scope: Mapped[str] = mapped_column(String(160), primary_key=True)
    checked_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    refreshed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    status: Mapped[str] = mapped_column(String(16), default="pending")
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)


class SourceFact(Base):
    """Scalar, path-addressable source value; no JSON payload is stored."""

    __tablename__ = "source_facts"
    __table_args__ = (
        UniqueConstraint(
            "source",
            "steam_app_id",
            "scope",
            "path",
            name="uq_source_fact_path",
        ),
        Index("ix_source_facts_game_scope", "source", "steam_app_id", "scope"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    source: Mapped[str] = mapped_column(String(32), index=True)
    steam_app_id: Mapped[int] = mapped_column(Integer, index=True)
    scope: Mapped[str] = mapped_column(String(160), index=True)
    path: Mapped[str] = mapped_column(String(512))
    value_type: Mapped[str] = mapped_column(String(16))
    value_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    value_int: Mapped[int | None] = mapped_column(Integer, nullable=True)
    value_float: Mapped[float | None] = mapped_column(Float, nullable=True)
    value_bool: Mapped[bool | None] = mapped_column(Boolean, nullable=True)


class SourceDiagnostic(Base):
    __tablename__ = "source_diagnostics"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    source: Mapped[str] = mapped_column(String(32), index=True)
    steam_app_id: Mapped[int] = mapped_column(Integer, index=True)
    scope: Mapped[str] = mapped_column(String(160), index=True)
    code: Mapped[str] = mapped_column(String(64))
    message: Mapped[str] = mapped_column(Text)
    observed_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class ScraperDatabase:
    """Async SQLite database holding normalized source entities and facts."""

    def __init__(self, config: ScraperConfig | None = None) -> None:
        self.config = config or ScraperConfig.from_env()
        self.engine = create_async_engine(
            _async_database_url(self.config.database_url),
            connect_args={"timeout": 30},
            pool_pre_ping=True,
        )
        self.session_factory = async_sessionmaker(
            self.engine,
            class_=AsyncSession,
            expire_on_commit=False,
        )

    def session(self) -> AsyncSession:
        return self.session_factory()

    async def create_schema(self, *, steam_only: bool = False) -> None:
        # Register source-specific Steam tables before creating the shared
        # metadata. The import is local to avoid a module-level ORM cycle.
        from scraper.steam import orm as _steam_orm  # noqa: F401

        async with self.engine.begin() as connection:
            await connection.exec_driver_sql("PRAGMA foreign_keys=ON")
            await connection.exec_driver_sql("PRAGMA journal_mode=WAL")
            if steam_only:
                table_names = {
                    name
                    for name in Base.metadata.tables
                    if name.startswith("steam_")
                    or name in {"source_refreshes", "source_diagnostics"}
                }
                tables = [
                    table for name, table in Base.metadata.tables.items() if name in table_names
                ]
                await connection.run_sync(
                    lambda sync_connection: Base.metadata.create_all(
                        sync_connection,
                        tables=tables,
                    )
                )
            else:
                await connection.run_sync(Base.metadata.create_all)

    async def dispose(self) -> None:
        await self.engine.dispose()


def _async_database_url(url: str) -> str:
    if url.startswith("sqlite:///") and not url.startswith("sqlite+aiosqlite:///"):
        return url.replace("sqlite:///", "sqlite+aiosqlite:///", 1)
    if url == "sqlite://":
        return "sqlite+aiosqlite://"
    return url


__all__ = [
    "Base",
    "WikidataAlias",
    "ScraperDatabase",
    "WikidataEntity",
    "WikidataFact",
    "WikidataGame",
    "WikidataGameLink",
    "WikidataNameLookup",
    "WikidataNameLookupResult",
    "WikidataQualifier",
    "SourceDiagnostic",
    "SourceFact",
    "SourceRefresh",
]
