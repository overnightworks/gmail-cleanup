"""Fire RFC 8058 one-click unsubscribes for active marketing senders.

Filters senders-full.csv to rows where:
  - has_list_unsubscribe = yes
  - list_unsubscribe_post contains "One-Click"  (RFC 8058 support)
  - newest_date within --recent-days (default 365)
  - action != keep

Then POSTs `List-Unsubscribe=One-Click` to each URL.

Dry run by default; pass --execute to actually send.
"""

from __future__ import annotations

import argparse
import csv
import socket
import sys
import urllib.error
import urllib.request
from datetime import date, datetime, timedelta
from pathlib import Path

from .act import offers_one_click, one_click_request
from .paths import CSV_DIR, PATH_CONFINEMENT_HELP, resolve_within_directory

HTTP_TIMEOUT = 10


def load_candidates(csv_path: Path, recent_cutoff: date) -> list[dict[str, str]]:
    with csv_path.open("r", encoding="utf-8") as f:
        rows = list(csv.DictReader(f))

    candidates = []
    for row in rows:
        if row["action"].strip().lower() == "keep":
            continue
        if row["has_list_unsubscribe"] != "yes":
            continue
        if not offers_one_click(
            row.get("list_unsubscribe_url", ""), row.get("list_unsubscribe_post", "")
        ):
            continue
        try:
            newest = datetime.fromisoformat(row["newest_date"]).date()
        except (ValueError, KeyError):
            continue
        if newest < recent_cutoff:
            continue
        candidates.append(row)
    return candidates


def unsubscribe(url: str) -> tuple[bool, str]:
    try:
        with urllib.request.urlopen(one_click_request(url), timeout=HTTP_TIMEOUT) as resp:
            ok = 200 <= resp.status < 400
            return ok, f"HTTP {resp.status}"
    except urllib.error.HTTPError as e:
        return False, f"HTTP {e.code}"
    except (urllib.error.URLError, socket.timeout, TimeoutError) as e:
        return False, f"network: {e}"
    except Exception as e:
        return False, f"unexpected: {e}"


def main() -> None:
    parser = argparse.ArgumentParser(description="Fire one-click unsubscribes")
    parser.add_argument(
        "--csv",
        type=Path,
        default=CSV_DIR / "senders-full.csv",
        help=f"Sender CSV to read candidates from ({PATH_CONFINEMENT_HELP})",
    )
    parser.add_argument("--recent-days", type=int, default=365)
    parser.add_argument("--execute", action="store_true")
    args = parser.parse_args()
    try:
        csv_path = resolve_within_directory(args.csv, CSV_DIR)
    except ValueError as e:
        sys.exit(str(e))

    cutoff = date.today() - timedelta(days=args.recent_days)
    candidates = load_candidates(csv_path, cutoff)
    print(f"{len(candidates)} active senders with one-click unsubscribe (newest >= {cutoff})")

    if not args.execute:
        for row in candidates:
            print(f"  would unsubscribe {row['from_email']} (last seen {row['newest_date']}, {row['count']} msgs)")
        print("\nDRY RUN — re-run with --execute to actually unsubscribe.")
        return

    ok_count = 0
    fail_count = 0
    for row in candidates:
        ok, detail = unsubscribe(row["list_unsubscribe_url"])
        status = "OK" if ok else "FAIL"
        print(f"  [{status}] {row['from_email']} — {detail}")
        if ok:
            ok_count += 1
        else:
            fail_count += 1

    print(f"\nDone: {ok_count} unsubscribed, {fail_count} failed")


if __name__ == "__main__":
    main()
