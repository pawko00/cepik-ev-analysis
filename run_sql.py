"""
Runs the SQL files in sql/ in name order (01_..., 02_...), each in its own transaction.
A failing file (e.g. a failed test inside it) is rolled back and stops the run.

Usage:
    python run_sql.py                 # all files
    python run_sql.py 01_dim_location # only files whose name contains the given text
"""

import sys
from pathlib import Path

import psycopg

from load_postgres import read_env

SQL_DIR = Path(__file__).with_name("sql")


def main() -> None:
    pattern = sys.argv[1] if len(sys.argv) > 1 else ""
    files = [f for f in sorted(SQL_DIR.glob("*.sql")) if pattern in f.name]
    if not files:
        raise SystemExit(f"No SQL files matching '{pattern}' in {SQL_DIR}")

    env = read_env()
    with psycopg.connect(host=env["PGHOST"], port=env["PGPORT"], user=env["PGUSER"],
                         password=env["PGPASSWORD"], dbname=env["PGDATABASE"]) as con:
        notices = []        # RAISE INFO messages from the SQL files (e.g. test summaries), printed after each file
        con.add_notice_handler(
            lambda d: notices.append(d.message_primary) if d.severity_nonlocalized == "INFO" else None)
        for f in files:
            print(f"Running {f.name} ...", end=" ", flush=True)
            try:
                con.execute(f.read_text(encoding="utf-8"))
                con.commit()
                print("OK")
                for msg in notices:
                    print(f"    {msg}")
                notices.clear()
            except psycopg.Error as e:
                con.rollback()
                print("FAILED")
                raise SystemExit(f"{f.name}: {e}")


if __name__ == "__main__":
    main()
