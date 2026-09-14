import importlib.util
from pathlib import Path

import pytest


RECEIPT = Path(__file__).resolve().parents[1] / "scripts" / "verify_live_nfl_ops_receipt.py"


def _module():
    assert RECEIPT.is_file(), "a promoted artifact needs a Fly-side verification receipt"
    spec = importlib.util.spec_from_file_location("verify_live_nfl_ops_receipt", RECEIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class _Reader:
    def __init__(self, *, missing_game: bool = False, missing_aggregate: str | None = None) -> None:
        self.calls: list[tuple[str, str]] = []
        self.missing_game = missing_game
        self.missing_aggregate = missing_aggregate

    def query(self, sql: str, *, database: str):
        self.calls.append((sql, database))
        if 'FROM nfl_historical."nfl_player_stats_all"' in sql and "GROUP BY nfl_team, opponent_nfl_team" in sql:
            rows = [
                {"nfl_team": "CHI", "opponent_nfl_team": "DET", "rows": 21},
                {"nfl_team": "DET", "opponent_nfl_team": "CHI", "rows": 20},
                {"nfl_team": "DAL", "opponent_nfl_team": "NYG", "rows": 22},
                {"nfl_team": "NYG", "opponent_nfl_team": "DAL", "rows": 22},
            ]
            if not self.missing_game:
                return rows
            return rows[:2]
        for table in (
            "player_nfl_season",
            "player_nfl_season_all",
            "player_nfl_career",
            "player_nfl_career_all",
            "player_bio",
            "player_nfl_season_team",
            "player_nfl_season_team_all",
        ):
            if f'FROM nfl_historical."{table}" AS aggregate' in sql:
                return [{"refreshed_players": 80, "covered_players": 79 if table == self.missing_aggregate else 80}]
        raise AssertionError(f"unexpected SQL: {sql}")


SCOPE = {
    "year": 2026,
    "week": 1,
    "season_type": "REG",
    "game_date": "2026-09-13",
    "game_ids": ["2026_01_CHI_DET", "2026_01_DAL_NYG"],
}


def test_fly_receipt_proves_every_final_game_and_every_live_aggregate_is_present():
    module = _module()
    reader = _Reader()

    receipt = module.collect_fly_receipt(reader, SCOPE)

    assert receipt["weekly"] == {
        "game_ids": ["2026_01_CHI_DET", "2026_01_DAL_NYG"],
        "rows": 85,
    }
    assert receipt["aggregates"]["player_nfl_season"] == {
        "refreshed_players": 80,
        "covered_players": 80,
    }
    assert set(receipt["aggregates"]) == {
        "player_nfl_season",
        "player_nfl_season_all",
        "player_nfl_career",
        "player_nfl_career_all",
        "player_bio",
        "player_nfl_season_team",
        "player_nfl_season_team_all",
    }
    assert {database for _sql, database in reader.calls} == {"___ops"}
    assert all("___leagues" not in sql for sql, _database in reader.calls)


def test_fly_receipt_fails_closed_when_a_final_game_is_absent():
    module = _module()

    with pytest.raises(module.FlyReceiptError, match="game IDs"):
        module.collect_fly_receipt(_Reader(missing_game=True), SCOPE)


def test_fly_receipt_fails_closed_when_an_aggregate_misses_a_refreshed_player():
    module = _module()

    with pytest.raises(module.FlyReceiptError, match="player_nfl_career_all"):
        module.collect_fly_receipt(_Reader(missing_aggregate="player_nfl_career_all"), SCOPE)
