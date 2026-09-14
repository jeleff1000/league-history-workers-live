"""Extract KMFFL's external 2013-2014 source rows from a Fly snapshot."""

from __future__ import annotations

from pathlib import Path

import duckdb


SOURCE = Path("/data/___leagues.duckdb")
OUTPUT = Path("/tmp/kmffl_archive.duckdb")
TABLES = (
    "matchup",
    "player_fantasy",
    "draft",
    "transactions",
    "schedule",
    "league_settings",
)


def main() -> None:
    if not SOURCE.exists():
        raise FileNotFoundError(SOURCE)
    if OUTPUT.exists():
        OUTPUT.unlink()

    conn = duckdb.connect(str(OUTPUT))
    try:
        conn.execute("SET threads = 1")
        conn.execute("SET memory_limit = '768MB'")
        conn.execute(f"ATTACH '{SOURCE.as_posix()}' AS snapshot (READ_ONLY)")
        conn.execute("CREATE SCHEMA public")
        available = {
            row[0]
            for row in conn.execute(
                "SELECT table_name FROM information_schema.tables "
                "WHERE table_catalog = 'snapshot' AND table_schema = 'public'"
            ).fetchall()
        }
        for table in TABLES:
            if table not in available:
                continue
            columns = {
                row[0]
                for row in conn.execute(
                    "SELECT column_name FROM information_schema.columns "
                    "WHERE table_catalog = 'snapshot' AND table_schema = 'public' "
                    "AND table_name = ?",
                    [table],
                ).fetchall()
            }
            if not {"db_name", "year"}.issubset(columns):
                continue
            conn.execute(
                f"CREATE TABLE public.{table} AS "
                f"SELECT * FROM snapshot.public.{table} "
                "WHERE db_name = 'kmffl' AND year IN (2013, 2014)"
            )
            count = conn.execute(f"SELECT COUNT(*) FROM public.{table}").fetchone()[0]
            print(f"{table}: {count}")

        years = {
            int(row[0])
            for row in conn.execute("SELECT DISTINCT year FROM public.matchup").fetchall()
        }
        if years != {2013, 2014}:
            raise RuntimeError(f"snapshot does not contain both KMFFL archive years: {sorted(years)}")
        conn.execute("CHECKPOINT")
    finally:
        conn.close()


if __name__ == "__main__":
    main()
