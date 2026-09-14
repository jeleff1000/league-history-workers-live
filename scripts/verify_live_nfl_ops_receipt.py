#!/usr/bin/env python3
"""Verify a promoted live NFL Ops scope through Fly's read path only.

The builder's local artifact receipt proves what was packaged.  This separate
read-only verifier proves that every final game in that scope and each live
aggregate relation can be read from Fly after the atomic promotion.
"""

from __future__ import annotations

import argparse
import json
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any


class FlyReceiptError(RuntimeError):
    """The read path does not prove the promoted NFL scope is live."""


YEAR_SCOPED_AGGREGATES = (
    "player_nfl_season",
    "player_nfl_season_all",
    "player_nfl_season_team",
    "player_nfl_season_team_all",
)
ALL_AGGREGATES = (
    *YEAR_SCOPED_AGGREGATES,
    "player_nfl_career",
    "player_nfl_career_all",
    "player_bio",
)

# NFLverse schedule IDs use modern abbreviations while the historical table
# retains Stathead-era codes in its canonical team/opponent identity.
_NFLVERSE_TO_STATHEAD_TEAM = {
    "GB": "GNB",
    "KC": "KAN",
    "LA": "LAR",
    "NO": "NOR",
    "NE": "NWE",
    "SF": "SFO",
    "TB": "TAM",
}


def _normalized_scope(scope: dict[str, Any]) -> dict[str, Any]:
    try:
        year = int(scope["year"])
        week = int(scope["week"])
        season_type = str(scope["season_type"]).strip().upper()
        game_date = date.fromisoformat(str(scope["game_date"])).isoformat()
        game_ids = sorted({str(value).strip() for value in scope["game_ids"] if str(value).strip()})
    except (KeyError, TypeError, ValueError) as error:
        raise FlyReceiptError(f"invalid finalized refresh scope: {error}") from error
    if season_type not in {"REG", "POST"}:
        raise FlyReceiptError(f"invalid finalized refresh season type: {season_type!r}")
    if not game_ids:
        raise FlyReceiptError("finalized refresh scope has no game IDs")
    return {
        "year": year,
        "week": week,
        "season_type": season_type,
        "game_date": game_date,
        "game_ids": game_ids,
    }


def _weekly_filter(scope: dict[str, Any], *, alias: str = "") -> str:
    prefix = f"{alias}." if alias else ""
    return " AND ".join(
        (
            f"{prefix}year = {scope['year']}",
            f"{prefix}week = {scope['week']}",
            f"{prefix}season_type = '{scope['season_type']}'",
            f"{prefix}game_date = '{scope['game_date']}'",
        )
    )


def _historical_team_code(value: object) -> str:
    code = str(value).strip().upper()
    return _NFLVERSE_TO_STATHEAD_TEAM.get(code, code)


def _expected_game_pairs(game_ids: list[str]) -> set[tuple[str, str]]:
    """Expand NFLverse game IDs to the SuperTable's directed team-pair key."""
    pairs: set[tuple[str, str]] = set()
    for game_id in game_ids:
        pieces = game_id.split("_")
        if len(pieces) < 4 or not pieces[-2] or not pieces[-1]:
            raise FlyReceiptError(f"invalid finalized NFLverse game ID: {game_id!r}")
        away_team = _historical_team_code(pieces[-2])
        home_team = _historical_team_code(pieces[-1])
        pairs.add((away_team, home_team))
        pairs.add((home_team, away_team))
    return pairs


def _read_single(reader: Any, sql: str) -> dict[str, Any]:
    rows = reader.query(sql, database="___ops")
    if len(rows) != 1:
        raise FlyReceiptError(f"receipt query returned {len(rows)} rows instead of one")
    return rows[0]


def _aggregate_coverage_sql(table: str, scope: dict[str, Any]) -> str:
    year_predicate = f"AND aggregate.year = {scope['year']}" if table in YEAR_SCOPED_AGGREGATES else ""
    return f"""
        WITH refreshed AS (
          SELECT DISTINCT NFL_player_id
          FROM nfl_historical."nfl_player_stats_all"
          WHERE {_weekly_filter(scope)}
        )
        SELECT
          COUNT(DISTINCT refreshed.NFL_player_id) AS refreshed_players,
          COUNT(DISTINCT aggregate.NFL_player_id) AS covered_players
        FROM nfl_historical."{table}" AS aggregate
        RIGHT JOIN refreshed
          ON aggregate.NFL_player_id = refreshed.NFL_player_id
          {year_predicate}
    """


def collect_fly_receipt(reader: Any, scope: dict[str, Any]) -> dict[str, Any]:
    """Return a fail-closed Fly receipt for one finalized NFL date scope."""
    normalized = _normalized_scope(scope)
    weekly_rows = reader.query(
        f"""
        SELECT nfl_team, opponent_nfl_team, COUNT(*) AS rows
        FROM nfl_historical."nfl_player_stats_all"
        WHERE {_weekly_filter(normalized)}
        GROUP BY nfl_team, opponent_nfl_team
        ORDER BY nfl_team, opponent_nfl_team
        """,
        database="___ops",
    )
    observed_pair_rows = {
        (
            _historical_team_code(row.get("nfl_team", "")),
            _historical_team_code(row.get("opponent_nfl_team", "")),
        ): int(row.get("rows") or 0)
        for row in weekly_rows
        if str(row.get("nfl_team", "")).strip() and str(row.get("opponent_nfl_team", "")).strip()
    }
    expected_pairs = _expected_game_pairs(normalized["game_ids"])
    if set(observed_pair_rows) != expected_pairs:
        raise FlyReceiptError(
            "Fly weekly team/opponent pairs do not exactly match the finalized game IDs: "
            f"expected={sorted(expected_pairs)}, observed={sorted(observed_pair_rows)}"
        )
    if any(rows <= 0 for rows in observed_pair_rows.values()):
        raise FlyReceiptError("Fly weekly receipt includes a finalized team/opponent pair with zero player rows")

    aggregates: dict[str, dict[str, int]] = {}
    for table in ALL_AGGREGATES:
        coverage = _read_single(reader, _aggregate_coverage_sql(table, normalized))
        refreshed_players = int(coverage.get("refreshed_players") or 0)
        covered_players = int(coverage.get("covered_players") or 0)
        if refreshed_players <= 0 or covered_players != refreshed_players:
            raise FlyReceiptError(
                f"Fly aggregate {table} is missing refreshed players: "
                f"refreshed={refreshed_players}, covered={covered_players}"
            )
        aggregates[table] = {
            "refreshed_players": refreshed_players,
            "covered_players": covered_players,
        }

    return {
        "scope": normalized,
        "weekly": {
            "game_ids": normalized["game_ids"],
            "rows": sum(observed_pair_rows.values()),
        },
        "aggregates": aggregates,
        "verified_at": datetime.now(timezone.utc).isoformat(),
    }


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--scope", type=Path, required=True, help="ready refresh_scope.json emitted by discovery")
    parser.add_argument("--output", type=Path, required=True, help="Fly read-path JSON receipt")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    payload = json.loads(args.scope.read_text(encoding="utf-8"))
    if payload.get("status") != "ready" or not isinstance(payload.get("scope"), dict):
        raise FlyReceiptError("cannot verify Fly promotion without a ready refresh scope")

    from multi_league.core.readers.fly_reader import FlyReader

    receipt = collect_fly_receipt(FlyReader(), payload["scope"])
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(receipt, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps(receipt, sort_keys=True))
    return 0


if __name__ == "__main__":  # pragma: no cover - exercised by GitHub Actions
    raise SystemExit(main())
