"""Compatibility entry point for the active Steam-only benchmark.

The old combined benchmark started Wikidata and validated its tables. Wikidata
is deprecated for the current work, so the historical filename now delegates
to the Steam-only runner.
"""

from benchmark_steam import main, run

__all__ = ["main", "run"]


if __name__ == "__main__":
    main()
