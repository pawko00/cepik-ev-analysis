"""
Loads the raw files downloaded by download.py into PostgreSQL.

Creates the database from .env (if needed) and, inside it, the `cepik` schema:
    cepik.vehicles_raw      - all downloaded vehicles, every API field as text, deduplicated;
                              column names mirror the API (Polish) for traceability to the source
    cepik.v_vehicles        - typed view (dates, numbers) with English names and helper columns
    cepik.v_passenger_cars  - passenger cars only (the main subject of the analysis)

Safe to run repeatedly, e.g. while downloading is still in progress - the table is truncated
and reloaded, and views built on top of it (including your own) are kept.

Connection settings: .env file in the project folder (template: .env.example).

Usage:
    python load_postgres.py
"""

import json
import re
from pathlib import Path

import psycopg
from psycopg import sql

ROOT = Path(__file__).parent
RAW_DIR = ROOT / "data" / "raw"
ENV_PATH = ROOT / ".env"


def read_env() -> dict:
    if not ENV_PATH.exists():
        raise SystemExit("No .env file - copy .env.example to .env and fill in the connection settings.")
    env = {}
    for line in ENV_PATH.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            key, value = line.split("=", 1)
            env[key.strip()] = value.strip()
    return env


def snake(name: str) -> str:
    return re.sub(r"[^0-9a-z]+", "_", name.lower()).strip("_")


def as_text(value):
    if value is None:
        return None
    if isinstance(value, bool):
        return "true" if value else "false"
    return str(value)


def load_raw() -> tuple[list[str], list[tuple]]:
    records = {}
    for path in sorted(RAW_DIR.glob("*/*.json")):
        voiv = path.parent.name
        for rec in json.loads(path.read_text(encoding="utf-8"))["data"]:
            row = {snake(k): v for k, v in rec["attributes"].items()}
            row["api_voivodeship_code"] = voiv
            # the same vehicle can appear twice (e.g. quarterly and monthly pages after a split)
            records[rec["id"]] = row
    columns = sorted({k for row in records.values() for k in row})
    rows = [(vid, *(as_text(row.get(c)) for c in columns)) for vid, row in records.items()]
    return ["id", *columns], rows


FUNCTIONS = """
-- safe casts: return NULL for a bad value instead of failing the whole query
CREATE OR REPLACE FUNCTION cepik.try_date(t text) RETURNS date
LANGUAGE plpgsql IMMUTABLE AS $$
BEGIN RETURN t::date; EXCEPTION WHEN others THEN RETURN NULL; END $$;

CREATE OR REPLACE FUNCTION cepik.try_num(t text) RETURNS numeric
LANGUAGE plpgsql IMMUTABLE AS $$
BEGIN RETURN t::numeric; EXCEPTION WHEN others THEN RETURN NULL; END $$;
"""

# values (e.g. 'SAMOCHÓD OSOBOWY', 'NOWY ZAKUPIONY W KRAJU') stay in Polish, as in the source data
VIEWS = """
CREATE OR REPLACE VIEW cepik.v_vehicles AS
SELECT
    id,
    api_voivodeship_code,
    rejestracja_wojewodztwo                                   AS voivodeship,
    rejestracja_powiat                                        AS county,
    rejestracja_gmina                                         AS municipality,
    marka                                                     AS make,
    model,
    rodzaj_pojazdu                                            AS vehicle_type,
    podrodzaj_pojazdu                                         AS vehicle_subtype,
    pochodzenie_pojazdu                                       AS origin,
    pochodzenie_pojazdu LIKE 'NOWY%'                          AS is_new,
    pochodzenie_pojazdu LIKE '%IMPORT%'                       AS is_imported,
    cepik.try_num(rok_produkcji)::int                         AS production_year,
    cepik.try_date(data_pierwszej_rejestracji_w_kraju)        AS registration_date_pl,
    cepik.try_date(data_pierwszej_rejestracji)                AS first_registration_date,
    cepik.try_date(data_wyrejestrowania_pojazdu)              AS deregistration_date,
    przyczyna_wyrejestrowania_pojazdu                         AS deregistration_reason,
    cepik.try_num(moc_netto_silnika)                          AS power_kw,
    cepik.try_num(masa_wlasna)::int                           AS curb_weight_kg,
    cepik.try_num(liczba_miejsc_ogolem)::int                  AS seats
FROM cepik.vehicles_raw;

CREATE OR REPLACE VIEW cepik.v_passenger_cars AS
SELECT *,
    date_trunc('month', registration_date_pl)::date           AS registration_month,
    extract(year FROM registration_date_pl)::int              AS registration_year
FROM cepik.v_vehicles
WHERE vehicle_type = 'SAMOCHÓD OSOBOWY';
"""


def ensure_database(env: dict) -> None:
    """Creates the database from .env if it doesn't exist yet (connecting to the default 'postgres' db)."""
    params = dict(host=env["PGHOST"], port=env["PGPORT"], user=env["PGUSER"], password=env["PGPASSWORD"])
    with psycopg.connect(dbname="postgres", autocommit=True, **params) as con:
        exists = con.execute("SELECT 1 FROM pg_database WHERE datname = %s", (env["PGDATABASE"],)).fetchone()
        if not exists:
            con.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(env["PGDATABASE"])))
            print(f"Created database {env['PGDATABASE']}")


def main() -> None:
    env = read_env()
    ensure_database(env)
    columns, rows = load_raw()

    with psycopg.connect(host=env["PGHOST"], port=env["PGPORT"], user=env["PGUSER"],
                         password=env["PGPASSWORD"], dbname=env["PGDATABASE"]) as con:
        con.execute("CREATE SCHEMA IF NOT EXISTS cepik")
        existing = [r[0] for r in con.execute(
            "SELECT column_name FROM information_schema.columns "
            "WHERE table_schema = 'cepik' AND table_name = 'vehicles_raw' ORDER BY ordinal_position")]
        if existing and set(existing) != set(columns):
            # the API changed its set of fields - the table must be rebuilt; CASCADE drops dependent views
            print("WARNING: the set of columns has changed, rebuilding the table. "
                  "Your own views based on cepik.* will need to be recreated.")
            con.execute("DROP TABLE cepik.vehicles_raw CASCADE")
            existing = []
        if not existing:
            con.execute(sql.SQL("CREATE TABLE cepik.vehicles_raw ({}, PRIMARY KEY (id))").format(
                sql.SQL(", ").join(sql.SQL("{} text").format(sql.Identifier(c)) for c in columns)))
        else:
            # truncate instead of drop, so views built on this table survive
            con.execute("TRUNCATE cepik.vehicles_raw")

        copy_sql = sql.SQL("COPY cepik.vehicles_raw ({}) FROM STDIN").format(
            sql.SQL(", ").join(map(sql.Identifier, columns)))
        with con.cursor().copy(copy_sql) as copy:
            for row in rows:
                copy.write_row(row)

        con.execute(FUNCTIONS)
        con.execute(VIEWS)
        con.commit()

        print(f"Vehicles loaded: {len(rows):,}")
        for vehicle_type, n in con.execute(
                "SELECT vehicle_type, count(*) FROM cepik.v_vehicles GROUP BY 1 ORDER BY 2 DESC LIMIT 6"):
            print(f"  {vehicle_type}: {n:,}")
        first, last, n, counties = con.execute(
            "SELECT min(registration_date_pl), max(registration_date_pl), count(*), count(DISTINCT county) "
            "FROM cepik.v_passenger_cars").fetchone()
        print(f"Passenger cars: {n:,} (registered {first} – {last}, counties: {counties})")


if __name__ == "__main__":
    main()
