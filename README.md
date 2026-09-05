# gmail-cleanup

Two-phase Gmail inbox cleanup:

1. `recon.py` — read-only scan, groups messages by sender, flags which have `List-Unsubscribe` headers, dumps `senders.csv` sorted by volume.
2. You eyeball the CSV and mark each sender: `unsubscribe`, `trash`, `filter`, or `keep` (or leave blank = skip).
3. `act.py` — reads the reviewed CSV and acts. Dry run by default, `--execute` to apply.

## One-time Google Cloud Console setup (~5 min)

1. Go to <https://console.cloud.google.com/>.
2. **Create a project** (top-left project picker → "New Project"). Name it anything, e.g. `gmail-cleanup`.
3. **Enable the Gmail API**: APIs & Services → Library → search "Gmail API" → Enable.
4. **Configure OAuth consent screen**: APIs & Services → OAuth consent screen.
   - User type: **External**
   - App name: `gmail-cleanup` (or whatever)
   - User support email + developer contact email: your gmail
   - Scopes: skip (leave default)
   - Test users: **add your own gmail address** (the one you'll be cleaning up)
   - Publishing status: leave as **Testing** — that's fine for personal use.
5. **Create OAuth client**: APIs & Services → Credentials → Create Credentials → OAuth client ID.
   - Application type: **Desktop app**
   - Name: anything
   - After creating, click the download icon → save the JSON as `credentials.json` in this directory.

Done. You'll never touch the Cloud Console again unless Google changes something.

## Install

```bash
cd ~/git/gmail-cleanup
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

## Use

```bash
# 1. Recon (read-only). First run opens a browser — click "Allow".
python recon.py --days 90

# 2. Edit senders.csv — fill the 'action' column.
#    Values: unsubscribe | trash | filter | keep | (blank)

# 3. Dry run — shows what would happen.
python act.py --csv senders.csv

# 4. Actually do it.
python act.py --csv senders.csv --execute
```

## Actions

- **unsubscribe** — if the sender provides RFC 8058 one-click unsubscribe (`List-Unsubscribe-Post: List-Unsubscribe=One-Click`), script POSTs to it. Then trashes existing messages from that sender. If no one-click is available, script just prints the manual URL and trashes existing.
- **trash** — moves all messages from that sender to Trash (recoverable for 30 days). No unsubscribe attempted.
- **filter** — creates a Gmail filter that auto-trashes future mail from that sender, and trashes existing. Use this for senders without a `List-Unsubscribe` that keep mailing you (transactional-style spam).
- **keep** (or blank) — nothing happens.

## Scopes

- `recon.py` requests `gmail.readonly` → cached in `token_readonly.json`.
- `act.py` requests `gmail.modify` + `gmail.settings.basic` → cached in `token_modify.json`.

Split on purpose — recon can never modify your mailbox.

## Files

- `~/.secrets/gmail-cleanup/credentials.json` — OAuth client secret, lives outside the repo.
- `~/.secrets/gmail-cleanup/token_*.json` — cached user tokens, same folder.
- `senders.csv` — your working CSV. `.gitignore`d.
