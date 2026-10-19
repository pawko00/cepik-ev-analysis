"""
Downloads all electric vehicles (fuel type: ENERGIA ELEKTRYCZNA) first registered in Poland
from the CEPiK API, for every voivodeship, quarter by quarter.

Raw responses are saved to data/raw/<voivodeship>/<period>_p<page>.json. Pages that already
exist are skipped, so an interrupted run can simply be restarted.

CEPiK API quirks handled here:
- queries over long periods take tens of seconds and end with 504 -> quarterly queries,
  automatically split into months when the server can't cope,
- responses are sometimes cut off mid-way (HTTP 200, incomplete JSON) -> retry,
- rate limit reported as 429 or 404 with API-ERR-100009 -> wait 65 s,
- the server uses a DH key that is too short for modern OpenSSL -> lowered cipher level for this host.

Usage:
    python download.py                    # everything from START_YEAR until today
    python download.py --refresh 2026     # re-download a given year (the API keeps adding registrations)
"""

import argparse
import calendar
import json
import logging
import ssl
import sys
import time
from datetime import date
from pathlib import Path

import requests
from requests.adapters import HTTPAdapter

API = "https://api.cepik.gov.pl/pojazdy"
RAW_DIR = Path(__file__).with_name("data") / "raw"

# voivodeship codes used by the API (XX = vehicles without a voivodeship)
VOIVODESHIPS = ["02", "04", "06", "08", "10", "12", "14", "16",
                "18", "20", "22", "24", "26", "28", "30", "32", "XX"]
START_YEAR = 2018
PAGE_SIZE = 500

DELAY_SECONDS = 10         # pause between requests; the API has a strict rate limit
RATE_LIMIT_WAIT = 65       # the API asks to wait 60 s after hitting the limit
BUSY_WAIT = 15             # server busy (global connection limit)
MAX_TRIES = 6
TIMEOUT = 90

# parameter names and values are the API's own (Polish)
BASE_PARAMS = {
    "limit": PAGE_SIZE,
    "typ-daty": 1,                     # date of first registration in Poland
    "tylko-zarejestrowane": "false",   # include deregistered vehicles, otherwise earlier years are undercounted
    "pokaz-wszystkie-pola": "true",
    "filter[rodzaj-paliwa]": "ENERGIA ELEKTRYCZNA",
}

log = logging.getLogger("download")


class LegacyDHAdapter(HTTPAdapter):
    """The CEPiK server uses a DH key that modern OpenSSL rejects (DH_KEY_TOO_SMALL).
    We lower the cipher security level for this host only; the certificate is still verified."""

    def init_poolmanager(self, *args, **kwargs):
        ctx = ssl.create_default_context()
        ctx.set_ciphers("DEFAULT@SECLEVEL=1")
        kwargs["ssl_context"] = ctx
        return super().init_poolmanager(*args, **kwargs)


class PeriodTooHeavy(Exception):
    """The server can't handle a query for this period - it needs to be split."""


def fetch_page(session: requests.Session, params: dict) -> dict:
    timeouts, attempt = 0, 0
    while attempt < MAX_TRIES:
        attempt += 1
        try:
            r = session.get(API, params=params, timeout=TIMEOUT)
            body = r.text
            # rate limits don't use up attempts - it's a queue, not a failed request
            if "API-ERR-100009" in body:            # rate limit (sent with 429 or 404)
                log.warning("Rate limit hit, waiting %d s", RATE_LIMIT_WAIT)
                time.sleep(RATE_LIMIT_WAIT)
                attempt -= 1
                continue
            if "API-ERR-100011" in body:            # server busy
                log.warning("Server busy, waiting %d s", BUSY_WAIT)
                time.sleep(BUSY_WAIT)
                attempt -= 1
                continue
            if r.status_code in (502, 503, 504):
                raise requests.Timeout(f"HTTP {r.status_code}")
            r.raise_for_status()
            data = r.json()                         # a truncated response raises ValueError
            if "data" not in data:
                raise ValueError(f"no 'data' field: {body[:200]}")
            return data
        except requests.Timeout as e:
            timeouts += 1
            if timeouts >= 2 and params["data-od"][:6] != params["data-do"][:6]:
                raise PeriodTooHeavy from e         # longer than a month -> split
            wait = 5            # the server keeps computing after a timeout, a retry usually hits its cache
        except (requests.RequestException, ValueError) as e:
            wait = min(10 * 2 ** (attempt - 1), 300)
            log.warning("Attempt %d/%d failed (%s)", attempt, MAX_TRIES, str(e)[:120])
        log.info("Retrying in %d s", wait)
        time.sleep(wait)
    raise RuntimeError(f"Failed to fetch page after {MAX_TRIES} attempts: {params}")


def download_period(session, voiv: str, start: date, end: date, label: str) -> int:
    """Downloads all pages for one period. Returns the number of records."""
    out_dir = RAW_DIR / voiv
    out_dir.mkdir(parents=True, exist_ok=True)
    done_marker = out_dir / f"{label}.done"
    if done_marker.exists():
        return 0

    params = dict(BASE_PARAMS, **{
        "wojewodztwo": voiv,
        "data-od": start.strftime("%Y%m%d"),
        "data-do": end.strftime("%Y%m%d"),
    })
    page, n_records = 1, 0
    while True:
        path = out_dir / f"{label}_p{page:03d}.json"
        if path.exists():
            data = json.loads(path.read_text(encoding="utf-8"))
        else:
            data = fetch_page(session, dict(params, page=page))
            tmp = path.with_suffix(".tmp")      # atomic write: an interruption won't leave a truncated file
            tmp.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
            tmp.replace(path)
            time.sleep(DELAY_SECONDS)
        n_records += len(data["data"])
        if not data["data"] or not data.get("links", {}).get("next"):
            break
        page += 1

    # a period that hasn't ended yet stays unmarked - the API keeps adding registrations
    if end < date.today():
        done_marker.touch()
    log.info("voivodeship %s, %s: %d vehicles (%d pages)", voiv, label, n_records, page)
    return n_records


def quarters(year: int):
    for q in range(4):
        start = date(year, 3 * q + 1, 1)
        if start > date.today():
            return
        last_month = 3 * q + 3
        end = date(year, last_month, calendar.monthrange(year, last_month)[1])
        yield f"{year}Q{q + 1}", start, min(end, date.today())


def months(start: date, end: date):
    m = start
    while m <= end:
        last = date(m.year, m.month, calendar.monthrange(m.year, m.month)[1])
        yield f"{m.year}-{m.month:02d}", m, min(last, end)
        m = date(m.year + (m.month == 12), m.month % 12 + 1, 1)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--refresh", type=int, metavar="YEAR", help="delete and re-download a given year")
    args = parser.parse_args()

    if args.refresh:
        for voiv in VOIVODESHIPS:
            for f in (RAW_DIR / voiv).glob(f"{args.refresh}*"):
                f.unlink()
        log.info("Deleted downloaded data for %d, downloading again", args.refresh)

    session = requests.Session()
    session.mount("https://api.cepik.gov.pl/", LegacyDHAdapter())
    session.headers["User-Agent"] = "cepik-ev-analysis (educational project)"

    total = 0
    # newest years first - the most interesting data is available for analysis first
    for year in range(date.today().year, START_YEAR - 1, -1):
        for voiv in VOIVODESHIPS:
            for label, start, end in quarters(year):
                # quarter already split into months earlier?
                if (RAW_DIR / voiv / f"{label}.split").exists():
                    parts = months(start, end)
                else:
                    try:
                        total += download_period(session, voiv, start, end, label)
                        continue
                    except PeriodTooHeavy:
                        log.warning("voivodeship %s, %s: server can't cope, splitting into months", voiv, label)
                        (RAW_DIR / voiv / f"{label}.split").touch()
                        for f in (RAW_DIR / voiv).glob(f"{label}_p*.json"):
                            f.unlink()          # monthly pages replace the quarterly ones
                        parts = months(start, end)
                for m_label, m_start, m_end in parts:
                    total += download_period(session, voiv, m_start, m_end, m_label)
    log.info("Done. Records: %d", total)


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
        handlers=[logging.StreamHandler(sys.stdout),
                  logging.FileHandler(Path(__file__).with_name("download.log"), encoding="utf-8")],
    )
    main()
