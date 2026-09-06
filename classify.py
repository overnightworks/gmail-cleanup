"""Auto-classify senders.csv based on heuristics.

Rules (first match wins):
  1. Email matches KEEP_PATTERNS → keep  (invoices, security, bank, personal, tax)
  2. Email matches TRASH_PATTERNS → trash (known ephemeral/marketing senders)
  3. has_list_unsubscribe == "yes" → trash (RFC 8058 marketing)
  4. Domain suggests newsletter (mailing/news/newsletter/marketing subdomain) → trash
  5. Otherwise → keep (conservative default — review manually)
"""

from __future__ import annotations

import argparse
import csv
import json
import re
import sys
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

from auth import SECRETS_DIR
from paths import PATH_CONFINEMENT_HELP, SCRIPT_DIR, resolve_within_directory

# Local, gitignored file holding personal and third-party sender data; see
# personal_senders.example.json for its shape and README.md for setup.
PERSONAL_SENDERS_FILE = SECRETS_DIR / "personal_senders.json"
PERSONAL_SENDERS_EXAMPLE_FILE = SCRIPT_DIR / "personal_senders.example.json"

KEEP_PATTERNS = [
    r"^service@paypal\.de$",
    r"^noreply@paypal\.de$",
    r"^no-reply@inwx\.de$",
    r"@accounts\.google\.com$",
    r"@sparkasse",
    r"@emails\.dkb\.de$",
    r"@commerzbank",
    r"@ing\.de$",
    r"@dkb\.de$",
    r"^portal@elster\.de$",
    r"@buhl\.de$",
    r"^invoice\+statements@mail\.anthropic\.com$",
    r"^no-reply-.*@mail\.anthropic\.com$",
    r"^noreply@github\.com$",
    r"^notifications@github\.com$",
    r"@sektion@alpenverein",
    r"@apotheke\.com$",
    r"^kontakt@apodiscounter\.de$",
    r"^service@afbshop\.de$",
    r"^kundenservice@congstar\.de$",
    r"^contact@ngrok\.com$",
    r"^noreply@notify\.cloudflare\.com$",
    r"^website@huggingface\.co$",
    r"^no-reply@spotify\.com$",
    r"^noreply@lichess\.org$",
    r"^forum-noreply@hardwareluxx\.de$",
    r"^notifications@link\.com$",
    r"^no-reply@onelogin",
    r"^onedrive@infomail\.microsoft\.com$",
    r"@posteo\.",
    r"^kundeninformation@emails\.dkb\.de$",
    r"^steuerwelt@buhl\.de$",
    r"^sektion@alpenverein",
]

TRASH_PATTERNS = [
    r"@deals\.",
    r"@marketing\.",
    r"@offers\.",
    r"@outlet\.",
    r"@promo\.",
    r"@shop-de\.",
    r"@shopmail\.",
    r"@service\.ryanairemail\.com$",
    r"@ryanair",
    r"@ups\.com$",
    r"@dhl\.",
    r"@service\.dpd\.de$",
    r"@deutschepost\.de$",
    r"@lieferstatus\.",
    r"@service\.otto\.de$",
    r"@paketankuendigung\.",
    r"@hermesworld\.com$",
    r"@komoot\.de$",
    r"^location-history-noreply@google\.com$",
    r"@einreiseanmeldung\.de$",
    r"@lufthansa-group\.com$",
    r"@condor\.com$",
    r"@euplf\.eu$",
    r"@pokerstars\.eu$",
    r"@redditmail\.com$",
    r"@gotinder\.com$",
    r"@immowelt\.de$",
    r"@email\.ibood\.com$",
    r"@community\.blablacar\.com$",
    r"@blablacar\.com$",
    r"@hallux-forum\.de$",
    r"@peugeotboard\.de$",
    r"@mydealz\.de$",
    r"^bestellung-aktuell@amazon\.",
    r"@marketplace\.amazon",
    r"^noreply-purchases@youtube\.com$",
    r"^no-reply@youtube\.com$",
    r"@shoop\.de$",
    r"@bazaarvoice",
    r"@trustedshops\.com$",
    r"^rating-noreply@",
    r"@customerservice365\.com$",
    r"@gmxnet\.de$",
    r"@roller\.de$",
    r"@communication\.microsoft\.com$",
    r"@kontakt\.veepee\.de$",
    r"@apo\.euva\.net$",
    r"^meetings-noreply@google\.com$",
    r"@mobile\.de$",
    r"@team\.mobile\.de$",
    r"@de-info\.adidas\.com$",
    r"@online-shop\.asics\.com$",
    r"@elektroshopwagner\.de$",
    r"@proshop\.de$",
    r"@service\.paypal\.com$",
    r"\.lufthansa-group\.com$",
    r"\.aliexpress\.com$",
    r"@puma\.com$",
    r"^versandbestaetigung@",
    r"^angebote@",
    r"@thalia\.de$",
    r"@bosch-home\.com$",
    r"@shop\.apollo\.de$",
    r"@samsung\.com$",
    r"@lge\.com$",
    r"@notice\.2\.ismartlife",
    r"@ihrstromanbieter\.",
    r"@segursub\.com$",
    r"@e\.spotify\.com$",
    r"@news\.",
    r"@newsletter\.",
    r"@mailing\.",
    r"^mailings@",
    r"@notice\.2\.ismartlife\.me$",
    r"@segursub\.com$",
    r"@pilotprojekt-grundeinkommen\.de$",
    r"@rueckgabe@amazon",
    r"^rueckgabe@amazon",
    r"^prime@amazon\.",
    r"^payments-messages@amazon\.",
    r"^konto-aktualisierung@amazon\.",
    r"^amazonsmile-de@amazon",
    r"^no-reply-music@amazon",
    r"^digital-no-reply@amazon",
    r"^account-update@amazon\.",
    r"@uber\.com$",
    r"@mail-de\.home-connect\.com$",
    r"@noreply\.store\.de@bosch-home\.com$",
    r"^noreply\.store\.de@bosch-home",
    r"@discord\.com$",
    r"@blizzard\.com$",
    r"@ticket\.io$",
    r"@myfritz\.net$",
    r"@bosch-home\.com$",
    r"@adac\.de$",
    r"@dropbox\.com$",
    r"@ikea\.com$",
    r"@hemden\.de$",
    r"@jysk\.com$",
    r"@samsung-mail\.com$",
    r"@notice\.samsung-mail\.com$",
    r"@deutschebahn\.com$",
    r"@shoop\.de$",
    r"@condor\.com$",
    r"@adresskorrektur@buhl",
    r"@kleinanzeigen\.de$",
    r"@ebay-kleinanzeigen\.de$",
    r"@connect\.lieferando\.de$",
    r"@lieferando\.de$",
    r"^no-reply@lieferando",
    r"@update\.lieferando\.de$",
    r"@goflink\.com$",
    r"@lieferbuddy\.de$",
    r"@temu\.com$",
    r"@temuemail\.com$",
    r"@aliexpress\.com$",
    r"^shipment-tracking@amazon\.",
    r"^versandbestaetigung@amazon\.",
    r"^bestellbestaetigung@amazon\.",
    r"^order-update@amazon\.",
    r"^no-reply@amazon\.",
    r"^promotion.*@amazon\.",
    r"^auto-confirm@amazon\.",
    r"^noreply@dhl\.de$",
    r"^.*@paketankuendigung\.",
    r"^ebay@ebay\.com$",
    r"@notice\.aliexpress\.com$",
    r"@mywellness\.com$",
    r"@xing\.com$",
    r"@immobilienscout24\.de$",
    r"^noreply-travel@google\.com$",
    r"^googleplay-noreply@google\.com$",
    r"^noreply-maps-timeline@google\.com$",
    r"@ryanairemail\.com$",
    r"@de\.idealo\.com$",
    r"@mail\.elitepartner\.de$",
    r"@update\.strava\.com$",
    r"@mailing\.rewe\.de$",
    r"@emails\.paypal\.com$",
    r"@e\.lenstore\.com$",
    r"@g-star\.com$",
    r"^card@kcard\.kaufland\.de$",
    r"@shop4runners\.com$",
    r"@docmorris\.de$",
    r"@gutscheinbuch\.de$",
    r"@mailing\.bahn\.de$",
    r"@info\.fielmann\.de$",
    r"@mail\.bahncard\.bahn\.de$",
    r"@news\.paypal\.com$",
    r"@mailinbox\.",
    r"@info\.aliexpress\.com$",
    r"@notice\.aliexpress\.com$",
    r"@ggpoker\.de$",
    r"@message\.tipico\.de$",
    r"@news\.congstar\.de$",
    r"@news\.goflink\.com$",
    r"@bestellung@ordersmart\.de$",
    r"^bestellung@ordersmart\.de$",
    r"@soccerstore@geomix",
    r"@geomix\.at$",
    r"@premiumsim\.de$",
    r"@no-reply@test-er\.de$",
    r"@test-er\.de$",
    r"@booking\.com$",
    r"@afb-group\.eu$",
    r"@asics\.com$",
    r"@coinbase\.com$",
    r"@markenmehrwert\.com$",
    r"@eg\.expedia\.com$",
    r"@email\.openai\.com$",
    r"@communications\.paypal\.com$",
    r"@newsletter\.apodiscounter\.de$",
    r"@email\.ikk-classic\.de$",
    r"@service-mail\.zalando-lounge\.de$",
    r"@lounge\.zalando\.de$",
    r"@tx\.shoop\.de$",
    r"@grupotel\.com$",
    r"@moleqlar\.com$",
    r"@mjt\.lu$",
    r"@hubspotemail\.net$",
    r"@audible\.de$",
    r"@steampowered\.com$",
    r"@netto-online\.de$",
    r"@info\.asics\.com$",
    r"@huk24-info\.de$",
]

NEWSLETTER_SUBDOMAIN = re.compile(
    r"@(mail|mailing|news|newsletter|marketing|info|offers|hello|updates?|em|e|m|tx|promo|noreply)\."
)


@dataclass(frozen=True)
class PersonalSendersConfig:
    """Personal and third-party sender data, loaded from a local, gitignored file.

    ``personal_senders`` and ``trash_senders`` back setup_personal.py's label
    and trash actions; ``personal_keep_patterns`` extends KEEP_PATTERNS with
    the operator's own addresses.
    """

    personal_senders: tuple[str, ...]
    trash_senders: tuple[str, ...]
    personal_keep_patterns: tuple[str, ...]


def _malformed_config_error(path: Path, detail: str) -> ValueError:
    return ValueError(
        f"{path} {detail} — see {PERSONAL_SENDERS_EXAMPLE_FILE} for the expected shape."
    )


def _load_string_list_field(data: dict, field: str, path: Path) -> tuple[str, ...]:
    if field not in data:
        raise _malformed_config_error(path, f"is missing field {field!r}")
    value = data[field]
    if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
        raise _malformed_config_error(path, f"field {field!r} must be a list of strings")
    return tuple(value)


def load_personal_senders_config(path: Path | None = None) -> PersonalSendersConfig:
    """Load personal sender data from ``path`` (default: PERSONAL_SENDERS_FILE).

    Raises FileNotFoundError naming the file and the tracked example when it
    is missing, and ValueError naming the file when its content is not a JSON
    object or a field is missing or not a list of strings.
    """
    path = path or PERSONAL_SENDERS_FILE
    if not path.exists():
        raise FileNotFoundError(
            f"Missing {path}. Copy {PERSONAL_SENDERS_EXAMPLE_FILE} to {path} "
            "and fill in your own addresses — see README.md."
        )
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise _malformed_config_error(path, f"is not valid JSON ({exc})") from exc
    if not isinstance(data, dict):
        raise _malformed_config_error(path, "must contain a JSON object")
    return PersonalSendersConfig(
        personal_senders=_load_string_list_field(data, "personal_senders", path),
        trash_senders=_load_string_list_field(data, "trash_senders", path),
        personal_keep_patterns=_load_string_list_field(data, "personal_keep_patterns", path),
    )


def classify(email: str, has_unsub: str, personal_keep_patterns: Sequence[str] = ()) -> str:
    for pattern in (*KEEP_PATTERNS, *personal_keep_patterns):
        if re.search(pattern, email):
            return "keep"
    for pattern in TRASH_PATTERNS:
        if re.search(pattern, email):
            return "trash"
    if has_unsub == "yes":
        return "trash"
    if NEWSLETTER_SUBDOMAIN.search(email):
        return "trash"
    return "keep"


def main() -> None:
    parser = argparse.ArgumentParser(description="Auto-classify senders.csv")
    parser.add_argument(
        "--csv",
        type=Path,
        default=SCRIPT_DIR / "senders-full.csv",
        help=f"Sender CSV to classify ({PATH_CONFINEMENT_HELP})",
    )
    parser.add_argument(
        "--out",
        type=Path,
        default=None,
        help=f"Where to write the classified CSV, default in place ({PATH_CONFINEMENT_HELP})",
    )
    args = parser.parse_args()
    try:
        csv_path = resolve_within_directory(args.csv, SCRIPT_DIR)
        out_path = resolve_within_directory(args.out, SCRIPT_DIR) if args.out else csv_path
    except ValueError as e:
        sys.exit(str(e))

    config = load_personal_senders_config()

    with csv_path.open("r", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        rows = list(reader)
        fieldnames = reader.fieldnames or []

    counts = {"keep": 0, "trash": 0, "filter": 0}
    for row in rows:
        action = classify(row["from_email"], row["has_list_unsubscribe"], config.personal_keep_patterns)
        row["action"] = action
        counts[action] = counts.get(action, 0) + 1

    with out_path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)

    print(f"Wrote {out_path} — {len(rows)} senders")
    for action, count in sorted(counts.items(), key=lambda kv: -kv[1]):
        print(f"  {action}: {count}")


if __name__ == "__main__":
    main()
