"""Small live check for the machine-readable sources listed in GAME_DATA_SOURCES.md.

It deliberately requests only one known public record (mostly Portal, Steam 620),
does not authenticate, and prints HTTP status plus JSON shape.  It is a smoke test,
not a bulk collector.  Run from the repository root:

    .\\.venv\\Scripts\\python.exe analysis\\verify_game_data_sources.py
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal

import httpx


@dataclass(frozen=True)
class Probe:
    name: str
    method: Literal["GET", "POST"]
    url: str
    params: dict[str, str] | None = None
    body: Any | None = None
    headers: dict[str, str] | None = None


PROBES = (
    Probe(
        "Steam Store appdetails",
        "GET",
        "https://store.steampowered.com/api/appdetails",
        {"appids": "620", "l": "en", "cc": "us"},
    ),
    Probe(
        "Steam reviews",
        "GET",
        "https://store.steampowered.com/appreviews/620",
        {
            "json": "1",
            "filter": "recent",
            "language": "all",
            "purchase_type": "all",
            "num_per_page": "1",
        },
    ),
    Probe(
        "Steam news",
        "GET",
        "https://api.steampowered.com/ISteamNews/GetNewsForApp/v0002/",
        {"appid": "620", "count": "1", "maxlength": "64", "format": "json"},
    ),
    Probe(
        "Steam current players",
        "GET",
        "https://api.steampowered.com/ISteamUserStats/GetNumberOfCurrentPlayers/v0001/",
        {"appid": "620", "format": "json"},
    ),
    Probe(
        "IsThereAnyDeal Steam crosswalk",
        "POST",
        "https://api.isthereanydeal.com/lookup/id/shop/61/v1",
        body=["app/620"],
        headers={"Content-Type": "application/json"},
    ),
    Probe(
        "SteamSpy",
        "GET",
        "https://steamspy.com/api.php",
        {"request": "appdetails", "appid": "620"},
    ),
    Probe(
        "PCGamingWiki MediaWiki revision",
        "GET",
        "https://www.pcgamingwiki.com/w/api.php",
        {
            "action": "query",
            "titles": "Portal 2",
            "prop": "revisions",
            "rvslots": "main",
            "rvprop": "content|ids|timestamp",
            "format": "json",
            "formatversion": "2",
        },
    ),
    Probe(
        "VNDB Kana",
        "POST",
        "https://api.vndb.org/kana/release",
        body={
            # Doki Doki Literature Club!; this validates VNDB's exact Steam
            # release-link filter, rather than guessing by title.
            "filters": ["extlink", "=", ["steam", "698780"]],
            "fields": (
                "id,title,released,platforms,"
                "languages{lang,latin,main,title,mtl},"
                "producers{id,name,developer,publisher},"
                "extlinks{id,name,label,url},vns{id,title}"
            ),
            "results": 1,
        },
        headers={"Content-Type": "application/json"},
    ),
    Probe(
        "Wikidata SPARQL Steam ID",
        "GET",
        "https://query.wikidata.org/sparql",
        {
            "format": "json",
            "query": 'SELECT ?item WHERE { ?item wdt:P1733 "620" . } LIMIT 3',
        },
        headers={
            "User-Agent": (
                "game-oracle-source-research/1.0 "
                "(https://github.com/miskl/scraper; mailto:research@example.invalid)"
            ),
            "Accept": "application/sparql-results+json",
        },
    ),
    Probe(
        "ProtonDB summary",
        "GET",
        "https://www.protondb.com/api/v1/reports/summaries/620.json",
        headers={"User-Agent": "game-oracle-source-research/1.0"},
    ),
    Probe(
        "speedrun.com",
        "GET",
        "https://www.speedrun.com/api/v1/games",
        {"name": "Portal", "max": "1", "embed": "platforms,categories,levels"},
    ),
    Probe(
        "Apple iTunes Search API",
        "GET",
        "https://itunes.apple.com/lookup",
        {"id": "479516143", "entity": "software", "country": "us"},
    ),
    Probe(
        "GOG product API v2",
        "GET",
        "https://api.gog.com/v2/games/1207658924",
        {"locale": "en-US"},
    ),
    Probe(
        "Microsoft Store product API",
        "GET",
        "https://storeedgefd.dsx.mp.microsoft.com/v9.0/products/9NBLGGH2JHXJ",
        {"market": "US", "locale": "en-US", "deviceFamily": "Windows.Desktop"},
    ),
    Probe(
        "CheapShark",
        "GET",
        "https://www.cheapshark.com/api/1.0/games",
        {"title": "Portal"},
        headers={"User-Agent": "game-oracle-source-research/1.0"},
    ),
    Probe(
        "GitHub REST",
        "GET",
        "https://api.github.com/repos/ValveSoftware/Proton",
        headers={"Accept": "application/vnd.github+json"},
    ),
    Probe(
        "Internet Archive Advanced Search",
        "GET",
        "https://archive.org/advancedsearch.php",
        {
            "q": "collection:softwarelibrary_msdos AND title:DOOM",
            "fl[]": "identifier,title,creator,date,year,subject",
            "rows": "1",
            "output": "json",
        },
    ),
    Probe(
        "GLEIF LEI API",
        "GET",
        "https://api.gleif.org/api/v1/lei-records",
        {"filter[entity.legalName]": "Nintendo", "page[size]": "1"},
    ),
    Probe(
        "SEC company submissions",
        "GET",
        "https://data.sec.gov/submissions/CIK0000796343.json",
        headers={"User-Agent": "game-oracle-source-research research@example.invalid"},
    ),
    # Gate checks: the JSON service and auth mechanism are live, but these are
    # intentionally unauthenticated and therefore must *not* return game data.
    Probe("IGDB auth gate", "POST", "https://api.igdb.com/v4/games"),
    Probe(
        "RAWG auth gate",
        "GET",
        "https://api.rawg.io/api/games",
        {"search": "Portal", "page_size": "1"},
    ),
    Probe(
        "RetroAchievements auth gate",
        "GET",
        "https://retroachievements.org/API/API_GetGame.php",
        {"i": "1"},
    ),
    Probe("Nexus Mods auth gate", "GET", "https://api.nexusmods.com/v1/games.json"),
    Probe(
        "IsThereAnyDeal price/search auth gate",
        "GET",
        "https://api.isthereanydeal.com/games/search/v1",
        {"title": "Portal 2"},
    ),
)


def _shape(value: Any) -> str:
    if isinstance(value, dict):
        return "object keys=" + ", ".join(map(str, list(value)[:16]))
    if isinstance(value, list):
        first = _shape(value[0]) if value else ""
        return f"array len={len(value)} first=({first})"
    return type(value).__name__


def main() -> None:
    with httpx.Client(timeout=30, follow_redirects=True) as client:
        for probe in PROBES:
            try:
                response = client.request(
                    probe.method,
                    probe.url,
                    params=probe.params,
                    json=probe.body,
                    headers=probe.headers,
                )
                content_type = response.headers.get("content-type", "")
                try:
                    details = _shape(response.json())
                except ValueError:
                    details = f"non-JSON bytes={len(response.content)}"
                print(f"{probe.name}\tHTTP {response.status_code}\t{content_type}\t{details}")
            except httpx.HTTPError as exc:
                print(f"{probe.name}\tERROR\t{type(exc).__name__}: {exc}")


if __name__ == "__main__":
    main()
