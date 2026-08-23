from __future__ import annotations

import os
from dataclasses import dataclass

DEFAULT_DATABASE_URL = "sqlite+aiosqlite:///./scraper.sqlite3"
DEFAULT_TTL_SECONDS = 30 * 24 * 60 * 60
DEFAULT_USER_AGENT = (
    "game-scraper/1.0 (https://www.wikidata.org/wiki/Wikidata:Data_access)"
)


@dataclass(frozen=True, slots=True)
class ScraperConfig:
    """Runtime configuration for all ORM-backed source synchronizers."""

    database_url: str = DEFAULT_DATABASE_URL
    ttl_seconds: int = DEFAULT_TTL_SECONDS
    concurrency: int = 32
    batch_size: int = 50
    request_timeout_seconds: float = 35.0
    connect_timeout_seconds: float = 15.0
    user_agent: str = DEFAULT_USER_AGENT
    pcgamingwiki_min_interval_seconds: float = 2.1
    steamspy_min_interval_seconds: float = 1.0

    @classmethod
    def from_env(cls) -> ScraperConfig:
        config = cls(
            database_url=_env_value("SCRAPER_DATABASE_URL", DEFAULT_DATABASE_URL),
            ttl_seconds=_env_int("SCRAPER_TTL_SECONDS", DEFAULT_TTL_SECONDS),
            concurrency=_env_int("SCRAPER_CONCURRENCY", 32),
            batch_size=_env_int("SCRAPER_BATCH_SIZE", 50),
            request_timeout_seconds=_env_float("SCRAPER_REQUEST_TIMEOUT_SECONDS", 35.0),
            connect_timeout_seconds=_env_float("SCRAPER_CONNECT_TIMEOUT_SECONDS", 15.0),
            user_agent=_env_value("SCRAPER_USER_AGENT", DEFAULT_USER_AGENT),
            pcgamingwiki_min_interval_seconds=_env_float(
                "SCRAPER_PCGAMINGWIKI_MIN_INTERVAL_SECONDS",
                2.1,
            ),
            steamspy_min_interval_seconds=_env_float(
                "SCRAPER_STEAMSPY_MIN_INTERVAL_SECONDS",
                1.0,
            ),
        )
        config.validate()
        return config

    def validate(self) -> None:
        if self.ttl_seconds < 1:
            raise ValueError("SCRAPER_TTL_SECONDS must be positive")
        if self.concurrency < 1:
            raise ValueError("SCRAPER_CONCURRENCY must be positive")
        if not 1 <= self.batch_size <= 50:
            raise ValueError("SCRAPER_BATCH_SIZE must be between 1 and 50")
        if self.request_timeout_seconds <= 0 or self.connect_timeout_seconds <= 0:
            raise ValueError("SCRAPER request timeouts must be positive")
        if self.pcgamingwiki_min_interval_seconds < 0:
            raise ValueError("SCRAPER_PCGAMINGWIKI_MIN_INTERVAL_SECONDS cannot be negative")
        if self.steamspy_min_interval_seconds < 0:
            raise ValueError("SCRAPER_STEAMSPY_MIN_INTERVAL_SECONDS cannot be negative")
        if not self.user_agent.strip():
            raise ValueError("SCRAPER_USER_AGENT cannot be empty")


def _env_value(name: str, default: str) -> str:
    return os.getenv(name, default)


def _env_int(name: str, default: int) -> int:
    value = os.getenv(name)
    if value is None:
        return default
    try:
        return int(value)
    except ValueError as exc:
        raise ValueError(f"{name} must be an integer") from exc


def _env_float(
    name: str,
    default: float,
) -> float:
    value = os.getenv(name)
    if value is None:
        return default
    try:
        return float(value)
    except ValueError as exc:
        raise ValueError(f"{name} must be a number") from exc


__all__ = [
    "DEFAULT_DATABASE_URL",
    "DEFAULT_TTL_SECONDS",
    "ScraperConfig",
]
