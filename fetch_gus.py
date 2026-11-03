"""
Downloads reference data from the GUS Local Data Bank (BDL, Statistics Poland) and loads it
into PostgreSQL, schema `gus`:
    gus.voivodeship   - 16 voivodeships with TERYT codes
    gus.county        - counties (powiaty) with TERYT codes and type (city / rural)

Raw API responses are cached in data/gus/ - delete the folder to download them again.

API docs: https://bdl.stat.gov.pl/api/v1/swagger (anonymous access, rate-limited)

Usage:
    python fetch_gus.py
"""

import json
import time
from pathlib import Path

import psycopg
import requests

from load_postgres import read_env

API = "https://bdl.stat.gov.pl/api/v1"
CACHE_DIR = Path(__file__).with_name("data") / "gus"
PAGE_SIZE = 100

LEVEL_VOIVODESHIP = 2
LEVEL_COUNTY = 5


def get_units(level: int) -> list[dict]:
    """All BDL units of a given level, cached as JSON."""
    cache = CACHE_DIR / f"units_level{level}.json"
    if cache.exists():
        return json.loads(cache.read_text(encoding="utf-8"))

    units, page = [], 0
    while True:
        r = requests.get(f"{API}/units", timeout=60,
                         params={"level": level, "page-size": PAGE_SIZE, "page": page, "format": "json"})
        r.raise_for_status()
        data = r.json()
        units += data["results"]
        if len(units) >= data["totalRecords"]:
            break
        page += 1
        time.sleep(1)       # anonymous BDL access is rate-limited

    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    cache.write_text(json.dumps(units, ensure_ascii=False, indent=1), encoding="utf-8")
    return units


def teryt_voivodeship(bdl_id: str) -> str:
    # BDL id layout: 2 digits macroregion, 2 digits voivodeship, 2 digits region,
    # 1 digit subregion, 2 digits county, 3 digits gmina -> TERYT county = voivodeship + county
    return bdl_id[2:4]


def teryt_county(bdl_id: str) -> str:
    return bdl_id[2:4] + bdl_id[7:9]


SCHEMA = """
CREATE SCHEMA IF NOT EXISTS gus;

CREATE TABLE IF NOT EXISTS gus.voivodeship (
    teryt       char(2) PRIMARY KEY,
    bdl_id      char(12) NOT NULL UNIQUE,
    name        text NOT NULL
);

CREATE TABLE IF NOT EXISTS gus.county (
    teryt               char(4) PRIMARY KEY,
    bdl_id              char(12) NOT NULL UNIQUE,
    name                text NOT NULL,          -- as in BDL, e.g. 'Powiat m. Lublin'
    short_name          text NOT NULL,          -- e.g. 'Lublin', 'lubelski'
    county_type         text NOT NULL CHECK (county_type IN ('city', 'rural')),
    voivodeship_teryt   char(2) NOT NULL REFERENCES gus.voivodeship
);
"""


def short_name(name: str) -> str:
    for prefix in ("Powiat m. st. ", "Powiat m. ", "Powiat "):
        if name.startswith(prefix):
            return name[len(prefix):].removesuffix(" od 2013")
    return name


def main() -> None:
    voivodeships = get_units(LEVEL_VOIVODESHIP)
    counties = get_units(LEVEL_COUNTY)

    # BDL keeps historical units: 'Powiat m. Wałbrzych do 2002' (city status lost in 2002, regained
    # in 2013 - filtered by name) and 'Powiat warszawski' (abolished in 2002, replaced by the city of Warsaw)
    historical = {"1431"}
    current = [c for c in counties
               if " do 20" not in c["name"] and teryt_county(c["id"]) not in historical]

    env = read_env()
    with psycopg.connect(host=env["PGHOST"], port=env["PGPORT"], user=env["PGUSER"],
                         password=env["PGPASSWORD"], dbname=env["PGDATABASE"]) as con:
        con.execute(SCHEMA)
        con.execute("TRUNCATE gus.county, gus.voivodeship")
        with con.cursor() as cur:
            cur.executemany(
                "INSERT INTO gus.voivodeship (teryt, bdl_id, name) VALUES (%s, %s, %s)",
                [(teryt_voivodeship(v["id"]), v["id"], v["name"]) for v in voivodeships])
            cur.executemany(
                "INSERT INTO gus.county (teryt, bdl_id, name, short_name, county_type, voivodeship_teryt) "
                "VALUES (%s, %s, %s, %s, %s, %s)",
                [(teryt_county(c["id"]), c["id"], c["name"], short_name(c["name"]),
                  "city" if c["kind"] == "2" else "rural", teryt_voivodeship(c["id"])) for c in current])
        con.commit()

        n_city, n_rural = con.execute(
            "SELECT count(*) FILTER (WHERE county_type = 'city'), count(*) FILTER (WHERE county_type = 'rural') "
            "FROM gus.county").fetchone()
        print(f"Voivodeships: {len(voivodeships)}, counties: {n_city + n_rural} "
              f"({n_city} city, {n_rural} rural)")


if __name__ == "__main__":
    main()
