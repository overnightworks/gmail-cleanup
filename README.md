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
   - After creating, click the download icon → save the JSON as
     `~/.secrets/gmail-cleanup/credentials.json` (create the directory first if it
     doesn't exist yet).

Done. You'll never touch the Cloud Console again unless Google changes something.

## Install

Prerequisite: [uv](https://docs.astral.sh/uv/getting-started/installation/).

```bash
cd ~/git/gmail-cleanup
uv sync --locked --no-build
source .venv/bin/activate
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

## Tests

```bash
uv sync --locked --no-build --group dev
uv run --locked --no-build pytest
```

CI runs the same commands, then uploads coverage to SonarCloud and fails on any open finding in scope.

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
- `~/.secrets/gmail-cleanup/personal_senders.json` — your personal and third-party sender
  data (see below). `.gitignore`d as a fallback if it ever ends up in the repo directory.
- `senders.csv` — your working CSV. `.gitignore`d.

## Personal senders

`classify.py` and `setup_personal.py` read personal addresses, the trash-list
of old acquaintances, and extra keep-patterns for your own addresses from
`~/.secrets/gmail-cleanup/personal_senders.json` — this keeps other people's
addresses out of tracked source. Set it up once:

```bash
mkdir -p ~/.secrets/gmail-cleanup
cp personal_senders.example.json ~/.secrets/gmail-cleanup/personal_senders.json
# edit ~/.secrets/gmail-cleanup/personal_senders.json with your own addresses
```

The file has three fields:

- `personal_senders` — addresses `setup_personal.py` labels "Personal" and filters.
- `trash_senders` — addresses `setup_personal.py` trashes outright.
- `personal_keep_patterns` — extra regexes `classify.py` treats as keep, alongside
  the generic service patterns already in `KEEP_PATTERNS`.

Running either script without this file fails immediately with an error naming
the missing file and the example.
