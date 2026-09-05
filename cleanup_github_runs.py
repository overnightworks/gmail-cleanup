"""One-off: trash GitHub 'Run failed' emails + create auto-trash filter."""

from auth import SCRIPT_DIR, get_credentials
from googleapiclient.discovery import build

SCOPES = [
    "https://www.googleapis.com/auth/gmail.modify",
    "https://www.googleapis.com/auth/gmail.settings.basic",
]
QUERY = 'from:notifications@github.com subject:"Run failed"'


def main() -> None:
    creds = get_credentials(SCOPES, SCRIPT_DIR / "token_modify.json")
    svc = build("gmail", "v1", credentials=creds)

    total = 0
    page_token = None
    while True:
        resp = (
            svc.users()
            .messages()
            .list(userId="me", q=QUERY, maxResults=500, pageToken=page_token)
            .execute()
        )
        msgs = resp.get("messages", [])
        if not msgs:
            break
        ids = [m["id"] for m in msgs]
        svc.users().messages().batchModify(
            userId="me",
            body={"ids": ids, "addLabelIds": ["TRASH"], "removeLabelIds": ["INBOX"]},
        ).execute()
        total += len(ids)
        print(f"  trashed {total} so far")
        page_token = resp.get("nextPageToken")
        if not page_token:
            break

    print(f"Trashed {total} workflow-failure emails.")

    filter_body = {
        "criteria": {"from": "notifications@github.com", "subject": "Run failed"},
        "action": {"addLabelIds": ["TRASH"], "removeLabelIds": ["INBOX"]},
    }
    f = svc.users().settings().filters().create(userId="me", body=filter_body).execute()
    filter_id = f["id"]
    print(f"Filter created: id={filter_id}")


if __name__ == "__main__":
    main()
