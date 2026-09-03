"""Measure field availability in a local Steam AppID cohort through Wikidata.

This deliberately measures direct `wdt:` values, not truth: a value can still
be unreferenced or wrong.  Supply a real, monitored contact in User-Agent.

Example:
    .\\.venv\\Scripts\\python.exe analysis\\audit_wikidata_coverage.py ^
      --user-agent "my-game-oracle/1.0 (mailto:data@example.com)"
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import httpx

PROPERTIES = {
    "P31": "instance_of",
    "P1476": "title",
    "P577": "publication_date",
    "P178": "developer",
    "P123": "publisher",
    "P400": "platform",
    "P136": "genre",
    "P179": "series",
    "P155": "follows",
    "P156": "followed_by",
    "P144": "based_on",
    "P306": "operating_system",
    "P407": "language_of_work",
    "P57": "director",
    "P86": "composer",
    "P58": "screenwriter",
    "P725": "voice_actor",
    "P2130": "capital_cost",
    "P2139": "total_revenue",
    "P2121": "price",
}


def _bindings(client: httpx.Client, query: str) -> list[dict[str, dict[str, str]]]:
    response = client.get(
        "https://query.wikidata.org/sparql",
        params={"query": query, "format": "json"},
        headers={"Accept": "application/sparql-results+json"},
    )
    response.raise_for_status()
    return response.json()["results"]["bindings"]


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--appids-file", type=Path, default=Path("appids.txt"))
    parser.add_argument("--limit", type=int, default=100)
    parser.add_argument("--user-agent", required=True)
    args = parser.parse_args()

    appids = [
        line.strip()
        for line in args.appids_file.read_text(encoding="utf-8").splitlines()
        if line.strip().isdigit()
    ][: args.limit]
    if not appids:
        raise SystemExit("No numeric Steam AppIDs in the selected cohort")

    steam_values = " ".join(f'"{appid}"' for appid in appids)
    properties = " ".join(f"wdt:{property_id}" for property_id in PROPERTIES)
    headers = {"User-Agent": args.user_agent}
    with httpx.Client(timeout=60, headers=headers, follow_redirects=True) as client:
        totals = _bindings(
            client,
            "SELECT (COUNT(DISTINCT ?steam) AS ?matched_appids) "
            "(COUNT(DISTINCT ?item) AS ?qids) WHERE { "
            f"VALUES ?steam {{ {steam_values} }} ?item wdt:P1733 ?steam }}",
        )[0]
        coverage_rows = _bindings(
            client,
            "SELECT ?property (COUNT(DISTINCT ?item) AS ?items) "
            "(COUNT(?value) AS ?values) WHERE { "
            f"VALUES ?steam {{ {steam_values} }} VALUES ?property {{ {properties} }} "
            "?item wdt:P1733 ?steam; ?property ?value "
            "} GROUP BY ?property ORDER BY DESC(?items)",
        )

    qids = int(totals["qids"]["value"])
    coverage = {}
    for row in coverage_rows:
        property_id = row["property"]["value"].rsplit("/", 1)[-1]
        coverage[PROPERTIES[property_id]] = {
            "property_id": property_id,
            "qids_with_value": int(row["items"]["value"]),
            "direct_values": int(row["values"]["value"]),
            "qids_with_value_percent": round(100 * int(row["items"]["value"]) / qids, 1),
        }
    print(
        json.dumps(
            {
                "input_appids": len(appids),
                "matched_appids": int(totals["matched_appids"]["value"]),
                "unique_qids": qids,
                "coverage": coverage,
                "warning": (
                    "Direct-value coverage only; inspect statement rank, references, "
                    "qualifiers and precision before promotion."
                ),
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
