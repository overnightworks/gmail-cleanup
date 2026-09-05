"""Gmail act — read reviewed senders.csv, unsubscribe / trash / filter per row."""

from __future__ import annotations

import argparse
import csv
import sys
import urllib.error
import urllib.request
from pathlib import Path

from googleapiclient.discovery import build
from googleapiclient.errors import HttpError

from auth import SCRIPT_DIR, get_credentials

MODIFY_SCOPES = [
    "https://www.googleapis.com/auth/gmail.modify",
    "https://www.googleapis.com/auth/gmail.settings.basic",
]
TOKEN_FILE = SCRIPT_DIR / "token_modify.json"

ACTION_UNSUBSCRIBE = "unsubscribe"
ACTION_TRASH = "trash"
ACTION_FILTER = "filter"
ACTION_KEEP = "keep"
VALID_ACTIONS = {ACTION_UNSUBSCRIBE, ACTION_TRASH, ACTION_FILTER, ACTION_KEEP, ""}


def one_click_unsubscribe(url: str, has_post_header: bool) -> bool:
    """RFC 8058 one-click POST if List-Unsubscribe-Post header was present."""
    if not url.startswith("http"):
        return False
    if not has_post_header:
        return False
    try:
        req = urllib.request.Request(
            url, data=b"List-Unsubscribe=One-Click", method="POST"
        )
        with urllib.request.urlopen(req, timeout=10) as resp:
            return 200 <= resp.status < 400
    except (urllib.error.URLError, TimeoutError) as e:
        print(f"    one-click POST failed: {e}")
        return False


def trash_messages_from(service, from_email: str, execute: bool) -> int:
    query = f"from:{from_email}"
    page_token = None
    total = 0
    while True:
        resp = (
            service.users()
            .messages()
            .list(userId="me", q=query, maxResults=500, pageToken=page_token)
            .execute()
        )
        messages = resp.get("messages", [])
        if not messages:
            break
        ids = [m["id"] for m in messages]
        total += len(ids)
        if execute:
            service.users().messages().batchModify(
                userId="me",
                body={"ids": ids, "addLabelIds": ["TRASH"], "removeLabelIds": ["INBOX"]},
            ).execute()
        page_token = resp.get("nextPageToken")
        if not page_token:
            break
    return total


def create_auto_trash_filter(service, from_email: str, execute: bool) -> None:
    body = {
        "criteria": {"from": from_email},
        "action": {"addLabelIds": ["TRASH"], "removeLabelIds": ["INBOX"]},
    }
    if execute:
        service.users().settings().filters().create(userId="me", body=body).execute()


def process_row(service, row: dict[str, str], execute: bool) -> str:
    action = row["action"].strip().lower()
    if action not in VALID_ACTIONS:
        print(f"[?] {row['from_email']}: unknown action '{action}', skipping")
        return "skip"
    if action in ("", ACTION_KEEP):
        return action or "skip"

    email = row["from_email"]
    print(f"[{action}] {email} ({row['count']} msgs in scanned window)")

    if action == ACTION_UNSUBSCRIBE:
        if row["has_list_unsubscribe"] == "yes":
            ok = one_click_unsubscribe(
                row["list_unsubscribe_url"],
                bool(row.get("list_unsubscribe_post", "").strip()),
            ) if execute else False
            if execute:
                print(f"    one-click: {'ok' if ok else 'not sent (no Post header or HTTP fail)'}")
            if row["list_unsubscribe_url"] and not row.get("list_unsubscribe_post"):
                print(f"    manual URL (no one-click): {row['list_unsubscribe_url']}")
        else:
            print("    no List-Unsubscribe header — trashing only")
        n = trash_messages_from(service, email, execute)
        print(f"    trashed {n} existing messages")
        return action

    if action == ACTION_TRASH:
        n = trash_messages_from(service, email, execute)
        print(f"    trashed {n} messages")
        return action

    if action == ACTION_FILTER:
        create_auto_trash_filter(service, email, execute)
        n = trash_messages_from(service, email, execute)
        print(f"    filter created + trashed {n} existing")
        return action

    return "skip"


def run(csv_path: Path, execute: bool) -> None:
    creds = get_credentials(MODIFY_SCOPES, TOKEN_FILE)
    service = build("gmail", "v1", credentials=creds)

    with csv_path.open("r", encoding="utf-8") as f:
        rows = list(csv.DictReader(f))

    counts = {a: 0 for a in VALID_ACTIONS if a}
    counts["skip"] = 0
    for row in rows:
        try:
            result = process_row(service, row, execute)
        except HttpError as e:
            print(f"    API error: {e}")
            counts["skip"] += 1
            continue
        counts[result] = counts.get(result, 0) + 1

    print("\nSummary:")
    for k, v in counts.items():
        if v:
            print(f"  {k}: {v}")
    if not execute:
        print("\nDRY RUN — nothing changed. Re-run with --execute to apply.")


def main() -> None:
    parser = argparse.ArgumentParser(description="Act on reviewed senders.csv")
    parser.add_argument("--csv", type=Path, default=SCRIPT_DIR / "senders.csv")
    parser.add_argument(
        "--execute",
        action="store_true",
        help="Actually perform actions (default: dry run)",
    )
    args = parser.parse_args()
    if not args.csv.exists():
        sys.exit(f"Missing {args.csv}. Run recon.py first.")
    run(args.csv, args.execute)


if __name__ == "__main__":
    main()
