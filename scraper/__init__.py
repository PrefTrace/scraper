"""Async game data scraper application."""

from .api import scrape
from .models import Game

__all__ = ["Game", "scrape"]

