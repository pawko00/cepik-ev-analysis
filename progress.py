"""
Shows download progress (download.py): how many periods (voivodeship × quarter) are complete,
progress per year, the number of downloaded vehicles and the estimated time remaining.

Usage:
    python progress.py
"""

import json
import subprocess
import sys
import time
from datetime import date, datetime
from pathlib import Path

from download import RAW_DIR, START_YEAR, VOIVODESHIPS, months, quarters

ROOT = Path(__file__).parent
PID_FILE = ROOT / "download.pid"


def period_complete(voiv_dir: Path, label: str) -> tuple[bool, float | None]:
    """A period is complete when it has a .done marker or its last page has no 'next' link
    (the latter applies to the current quarter, which download.py deliberately leaves unmarked).
    Also returns the completion time (mtime), used to estimate the download rate."""
    marker = voiv_dir / f"{label}.done"
    if marker.exists():
        return True, marker.stat().st_mtime
    pages = sorted(voiv_dir.glob(f"{label}_p*.json"))
    if pages:
        last = json.loads(pages[-1].read_text(encoding="utf-8"))
        if not last.get("links", {}).get("next") or not last["data"]:
            return True, pages[-1].stat().st_mtime
    return False, None


def quarter_status(voiv: str, label: str, start: date, end: date) -> tuple[bool, float | None]:
    voiv_dir = RAW_DIR / voiv
    if (voiv_dir / f"{label}.split").exists():   # quarter split into months
        results = [period_complete(voiv_dir, m) for m, _, _ in months(start, end)]
        times = [t for _, t in results if t]
        return all(ok for ok, _ in results), max(times) if times else None
    return period_complete(voiv_dir, label)


def process_running() -> bool:
    if not PID_FILE.exists():
        return False
    pid = PID_FILE.read_text().strip()
    try:
        out = subprocess.run(["tasklist", "/FI", f"PID eq {pid}", "/NH"], capture_output=True, text=True).stdout
        return pid in out
    except OSError:
        return False


def count_records() -> int:
    ids = set()
    for path in RAW_DIR.glob("*/*.json"):
        ids.update(r["id"] for r in json.loads(path.read_text(encoding="utf-8"))["data"])
    return len(ids)


def main() -> None:
    years = range(date.today().year, START_YEAR - 1, -1)
    per_year, done_times = {}, []
    total = done = 0
    for year in years:
        y_total = y_done = 0
        for voiv in VOIVODESHIPS:
            for label, start, end in quarters(year):
                ok, t = quarter_status(voiv, label, start, end)
                y_total += 1
                y_done += ok
                if t:
                    done_times.append(t)
        per_year[year] = (y_done, y_total)
        total += y_total
        done += y_done

    pct = 100 * done / total
    bar = "█" * int(pct / 2.5) + "░" * (40 - int(pct / 2.5))
    print(f"\n  Download: {'RUNNING' if process_running() else 'NOT RUNNING'}")
    print(f"  {bar} {pct:5.1f}%   ({done}/{total} periods)\n")

    for year, (y_done, y_total) in per_year.items():
        y_pct = 100 * y_done / y_total
        mark = "✓" if y_done == y_total else ("…" if y_done else " ")
        print(f"  {year}  {'█' * int(y_pct / 10):<10} {y_pct:5.1f}%  {mark}")

    # rate over the last hour (or since the start, if shorter)
    now = time.time()
    recent = [t for t in done_times if now - t < 3600]
    if len(recent) >= 3 and done < total:
        span = max(now - min(recent), 60)
        rate = len(recent) / span                     # periods per second
        eta_s = (total - done) / rate
        eta = datetime.fromtimestamp(now + eta_s)
        print(f"\n  Rate: {rate * 3600:.0f} periods/h, about {eta_s / 3600:.1f} h left "
              f"(done around {eta:%d.%m %H:%M})")
    elif done == total:
        print("\n  Everything downloaded! Run: python load_postgres.py")

    print(f"  Vehicles downloaded (all types): {count_records():,}\n")


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    main()
