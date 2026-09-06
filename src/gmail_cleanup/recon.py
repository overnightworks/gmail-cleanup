"""Gmail recon — read-only scan, group messages by sender, flag newsletters."""

from __future__ import annotations

import argparse
import csv
import sys
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from email.utils import parseaddr
from pathlib import Path

from googleapiclient.discovery import build
from googleapiclient.errors import HttpError

from . import module_command
from .auth import SECRETS_DIR, get_credentials
from .paths import CSV_DIR, PATH_CONFINEMENT_HELP, resolve_within_directory

READONLY_SCOPES = ["https://www.googleapis.com/auth/gmail.readonly"]
TOKEN_FILE = SECRETS_DIR / "token_readonly.json"

METADATA_HEADERS = [
    "From",
    "Subject",
    "List-Unsubscribe",
    "List-Unsubscribe-Post",
]

BATCH_SIZE = 100

CSV_FIELDNAMES = [
    "action",
    "from_email",
    "from_name",
    "count",
    "has_list_unsubscribe",
    "list_unsubscribe_url",
    "list_unsubscribe_post",
    "sample_subject",
    "oldest_date",
    "newest_date",
]
"""Columns of the sender CSV recon.py writes and act.py/unsubscribe.py read."""


@dataclass
class SenderStats:
    from_email: str
    from_name: str = ""
    count: int = 0
    has_list_unsubscribe: bool = False
    list_unsubscribe_url: str = ""
    list_unsubscribe_post: str = ""
    sample_subject: str = ""
    oldest_ts: int | None = None
    newest_ts: int | None = None


def parse_list_unsubscribe(header_value: str) -> str:
    """Return preferred (http if available, else mailto) URL from List-Unsubscribe."""
    http_url = ""
    mailto_url = ""
    for part in header_value.split(","):
        part = part.strip().strip("<>").strip()
        if part.startswith("http"):
            http_url = part
        elif part.startswith("mailto:"):
            mailto_url = part
    return http_url or mailto_url


def iter_message_ids(service, days: int, max_messages: int, include_spam_trash: bool):
    after_date = (datetime.now(timezone.utc) - timedelta(days=days)).strftime("%Y/%m/%d")
    query = f"-in:sent -in:drafts -in:chats after:{after_date}"
    page_token = None
    fetched = 0
    while fetched < max_messages:
        resp = (
            service.users()
            .messages()
            .list(
                userId="me",
                q=query,
                maxResults=min(500, max_messages - fetched),
                pageToken=page_token,
                includeSpamTrash=include_spam_trash,
            )
            .execute()
        )
        messages = resp.get("messages", [])
        for m in messages:
            yield m["id"]
            fetched += 1
            if fetched >= max_messages:
                return
        page_token = resp.get("nextPageToken")
        if not page_token:
            return


def _apply_headers(senders: dict[str, SenderStats], headers: dict[str, str]) -> None:
    name, email = parseaddr(headers.get("From", ""))
    if not email:
        return
    email = email.lower()
    stats = senders.setdefault(email, SenderStats(from_email=email))
    stats.count += 1
    if name and not stats.from_name:
        stats.from_name = name
    if not stats.sample_subject:
        stats.sample_subject = headers.get("Subject", "")[:80]
    lu = headers.get("List-Unsubscribe", "")
    if lu and not stats.has_list_unsubscribe:
        stats.has_list_unsubscribe = True
        stats.list_unsubscribe_url = parse_list_unsubscribe(lu)
        stats.list_unsubscribe_post = headers.get("List-Unsubscribe-Post", "")
    ts = int(headers["_internalDate"])
    if stats.oldest_ts is None or ts < stats.oldest_ts:
        stats.oldest_ts = ts
    if stats.newest_ts is None or ts > stats.newest_ts:
        stats.newest_ts = ts


MAX_RETRIES = 5
RETRY_BASE_DELAY = 0.5


def _run_batch(service, message_ids: list[str], senders: dict[str, SenderStats]) -> list[str]:
    retryable: list[str] = []

    def on_response(request_id, response, exception):
        idx = int(request_id) - 1
        mid = message_ids[idx]
        if exception is not None:
            status = getattr(getattr(exception, "resp", None), "status", None)
            if status in (429, 500, 502, 503, 504):
                retryable.append(mid)
            return
        headers = {h["name"]: h["value"] for h in response["payload"].get("headers", [])}
        headers["_internalDate"] = response.get("internalDate", "0")
        _apply_headers(senders, headers)

    batch = service.new_batch_http_request(callback=on_response)
    for mid in message_ids:
        batch.add(
            service.users().messages().get(
                userId="me",
                id=mid,
                format="metadata",
                metadataHeaders=METADATA_HEADERS,
            )
        )
    batch.execute()
    return retryable


def _fetch_pass(
    service, pending: list[str], senders: dict[str, SenderStats], total: int, attempt: int
) -> list[str]:
    """Run one sweep of ``pending`` in batches, returning the ids to retry."""
    retry_next: list[str] = []
    for start in range(0, len(pending), BATCH_SIZE):
        chunk = pending[start : start + BATCH_SIZE]
        try:
            retry_next.extend(_run_batch(service, chunk, senders))
        except HttpError as e:
            print(f"  full-batch error {start}: {e}")
            retry_next.extend(chunk)
        if attempt == 0 and (start + BATCH_SIZE) % 1000 < BATCH_SIZE:
            done = min(start + BATCH_SIZE, len(pending))
            print(f"  {done}/{total}")
    return retry_next


def _report_pass_result(attempt: int, retry_count: int) -> None:
    if attempt == 0:
        print(f"  pass 1 done: {retry_count} to retry")
    else:
        print(f"  retry pass {attempt}: {retry_count} still pending")


def aggregate(service, message_ids: list[str]) -> dict[str, SenderStats]:
    senders: dict[str, SenderStats] = {}
    total = len(message_ids)
    pending = list(message_ids)
    attempt = 0

    while pending and attempt <= MAX_RETRIES:
        retry_next = _fetch_pass(service, pending, senders, total, attempt)
        _report_pass_result(attempt, len(retry_next))
        pending = retry_next
        if not pending:
            break
        time.sleep(RETRY_BASE_DELAY * (2 ** attempt))
        attempt += 1

    if pending:
        print(f"  ({len(pending)} messages dropped after {MAX_RETRIES} retries)")
    return senders


def write_csv(senders: dict[str, SenderStats], output_csv: Path) -> None:
    sorted_senders = sorted(senders.values(), key=lambda s: s.count, reverse=True)
    with output_csv.open("w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(CSV_FIELDNAMES)
        for s in sorted_senders:
            writer.writerow(
                [
                    "",
                    s.from_email,
                    s.from_name,
                    s.count,
                    "yes" if s.has_list_unsubscribe else "no",
                    s.list_unsubscribe_url,
                    s.list_unsubscribe_post,
                    s.sample_subject,
                    datetime.fromtimestamp(s.oldest_ts / 1000, tz=timezone.utc).date()
                    if s.oldest_ts
                    else "",
                    datetime.fromtimestamp(s.newest_ts / 1000, tz=timezone.utc).date()
                    if s.newest_ts
                    else "",
                ]
            )


def run_recon(days: int, max_messages: int, output_csv: Path, include_spam_trash: bool) -> None:
    creds = get_credentials(READONLY_SCOPES, TOKEN_FILE)
    service = build("gmail", "v1", credentials=creds)

    print(f"Scanning last {days} days (up to {max_messages} messages)...")
    message_ids = list(iter_message_ids(service, days, max_messages, include_spam_trash))
    print(f"Found {len(message_ids)} messages. Fetching headers...")

    senders = aggregate(service, message_ids)
    write_csv(senders, output_csv)

    print(f"\nWrote {output_csv} — {len(senders)} unique senders.")
    print("Edit the 'action' column in the CSV: unsubscribe | trash | filter | keep")
    act_command = f"{module_command('act')} --csv senders.csv"
    print(f"Then: {act_command}           (dry run)")
    print(f"      {act_command} --execute (actually do it)")


def main() -> None:
    parser = argparse.ArgumentParser(description="Gmail inbox recon (read-only)")
    parser.add_argument("--days", type=int, default=90, help="Scan last N days (default: 90)")
    parser.add_argument(
        "--max", type=int, default=5000, help="Max messages to scan (default: 5000)"
    )
    parser.add_argument(
        "--out",
        type=Path,
        default=CSV_DIR / "senders.csv",
        help=f"Where to write the sender CSV ({PATH_CONFINEMENT_HELP})",
    )
    parser.add_argument(
        "--include-spam-trash",
        action="store_true",
        help="Also scan Spam and Trash (default: off)",
    )
    args = parser.parse_args()
    try:
        out_path = resolve_within_directory(args.out, CSV_DIR)
    except ValueError as e:
        sys.exit(str(e))
    run_recon(args.days, args.max, out_path, args.include_spam_trash)


if __name__ == "__main__":
    main()
