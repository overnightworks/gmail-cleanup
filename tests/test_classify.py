from __future__ import annotations

import csv
import json
import re
import sys
from collections.abc import Callable
from pathlib import Path

import pytest

from gmail_cleanup import classify as classify_script
from gmail_cleanup.classify import (
    PERSONAL_SENDERS_EXAMPLE_FILE,
    PersonalSendersConfig,
    classify,
    load_personal_senders_config,
)
from gmail_cleanup.recon import CSV_FIELDNAMES


def test_list_unsubscribe_header_trashes_an_unlisted_sender() -> None:
    """An unlisted sender with a List-Unsubscribe header is treated as marketing."""
    assert classify("updates@some-random-service.example.com", has_unsub="yes") == "trash"


def test_keep_pattern_wins_over_list_unsubscribe() -> None:
    """A keep-listed sender stays kept even when it also sends List-Unsubscribe."""
    assert classify("noreply@github.com", has_unsub="yes") == "keep"


def test_personal_keep_pattern_from_loaded_config_wins_over_list_unsubscribe() -> None:
    """A sender matched only by a loaded personal keep pattern is still kept."""
    config = load_personal_senders_config(PERSONAL_SENDERS_EXAMPLE_FILE)
    personal_address = "friend-1@example.com"

    assert classify(personal_address, has_unsub="yes") == "trash"
    assert (
        classify(personal_address, has_unsub="yes", personal_keep_patterns=config.personal_keep_patterns)
        == "keep"
    )


def test_load_personal_senders_config_reads_a_present_file(tmp_path: Path) -> None:
    path = tmp_path / "personal_senders.json"
    path.write_text(
        json.dumps(
            {
                "personal_keep_patterns": [r"^a@example\.com$"],
            }
        ),
        encoding="utf-8",
    )

    assert load_personal_senders_config(path) == PersonalSendersConfig(
        personal_keep_patterns=(r"^a@example\.com$",),
    )


def test_load_personal_senders_config_raises_a_clear_error_when_missing(tmp_path: Path) -> None:
    missing_path = tmp_path / "personal_senders.json"

    with pytest.raises(FileNotFoundError, match=re.escape(str(missing_path))) as refusal:
        load_personal_senders_config(missing_path)

    assert str(PERSONAL_SENDERS_EXAMPLE_FILE) in str(refusal.value)


@pytest.mark.parametrize(
    "content",
    [
        pytest.param("not json", id="not_json"),
        pytest.param(json.dumps({}), id="missing_field"),
        pytest.param(json.dumps(["a@example.com"]), id="top_level_not_an_object"),
    ],
)
def test_load_personal_senders_config_raises_a_clear_error_for_malformed_content(
    tmp_path: Path, content: str
) -> None:
    path = tmp_path / "personal_senders.json"
    path.write_text(content, encoding="utf-8")

    with pytest.raises(ValueError, match=re.escape(str(path))):
        load_personal_senders_config(path)


@pytest.mark.parametrize(
    "config_data",
    [
        pytest.param({"personal_keep_patterns": "a@example.com"}, id="string_instead_of_list"),
        pytest.param({"personal_keep_patterns": {"a": "b"}}, id="dict_instead_of_list"),
        pytest.param({"personal_keep_patterns": ["ok@example.com", 3]}, id="non_string_element"),
    ],
)
def test_load_personal_senders_config_raises_a_clear_error_for_wrong_typed_fields(
    tmp_path: Path, config_data: dict[str, object]
) -> None:
    """A hand-edited field that isn't a list of strings is rejected, not silently misread.

    Regression guard: ``tuple("old@example.com")`` would otherwise split the
    string into single-character patterns, each keeping senders in bulk.
    """
    path = tmp_path / "personal_senders.json"
    path.write_text(json.dumps(config_data), encoding="utf-8")

    with pytest.raises(ValueError, match=re.escape(str(path))):
        load_personal_senders_config(path)


def test_example_personal_senders_file_loads_as_a_valid_config() -> None:
    """The tracked example stays in sync with the loader's expected shape."""
    config = load_personal_senders_config(PERSONAL_SENDERS_EXAMPLE_FILE)

    assert config.personal_keep_patterns


@pytest.mark.parametrize(
    ("email", "has_unsub", "expected_action"),
    [
        pytest.param("service@paypal.de", "no", "keep", id="keep pattern"),
        pytest.param("promo@deals.example.com", "no", "trash", id="trash pattern"),
        pytest.param("digest@updates.example.com", "no", "trash", id="newsletter subdomain"),
        pytest.param("person@unlisted-company.example.com", "no", "keep", id="unknown sender"),
    ],
)
def test_classify_follows_the_first_matching_rule(
    email: str, has_unsub: str, expected_action: str
) -> None:
    assert classify(email, has_unsub=has_unsub) == expected_action


PERSONAL_FRIEND = "friend@example.com"
MARKETING_SENDER = "promo@deals.example.com"


def _classified_actions(csv_path: Path) -> dict[str, str]:
    """The action each sender in ``csv_path`` was classified as."""
    with csv_path.open(encoding="utf-8") as f:
        return {row["from_email"]: row["action"] for row in csv.DictReader(f)}


def _header_columns(csv_path: Path) -> list[str] | None:
    """The columns ``csv_path`` announces, as the next script in the chain reads them."""
    with csv_path.open(encoding="utf-8") as f:
        return csv.DictReader(f).fieldnames


@pytest.fixture
def senders_csv(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    write_csv_of_senders: Callable[[Path, list[dict[str, str]]], None],
) -> Callable[..., Path]:
    """Write an unclassified sender CSV inside the directory main() may read from."""
    monkeypatch.setattr(classify_script, "CSV_DIR", tmp_path)

    def write(*senders: str, name: str = "senders-full.csv") -> Path:
        csv_path = tmp_path / name
        write_csv_of_senders(
            csv_path,
            [{"from_email": sender, "has_list_unsubscribe": "no"} for sender in senders],
        )
        return csv_path

    return write


def test_main_classifies_every_sender_in_place(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    senders_csv: Callable[..., Path],
    personal_senders_config: Callable[..., None],
) -> None:
    """The personal keep patterns from the local config decide alongside the built-in rules."""
    personal_senders_config(personal_keep_patterns=[rf"^{re.escape(PERSONAL_FRIEND)}$"])
    csv_path = senders_csv(PERSONAL_FRIEND, MARKETING_SENDER)
    monkeypatch.setattr(sys, "argv", ["classify.py", "--csv", str(csv_path)])

    classify_script.main()

    assert _classified_actions(csv_path) == {PERSONAL_FRIEND: "keep", MARKETING_SENDER: "trash"}
    out = capsys.readouterr().out
    assert "2 senders" in out
    assert "keep: 1" in out
    assert "trash: 1" in out


def test_main_writes_to_the_out_file_and_leaves_the_input_unclassified(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    senders_csv: Callable[..., Path],
    personal_senders_config: Callable[..., None],
) -> None:
    personal_senders_config()
    csv_path = senders_csv(MARKETING_SENDER)
    out_path = tmp_path / "classified.csv"
    monkeypatch.setattr(
        sys, "argv", ["classify.py", "--csv", str(csv_path), "--out", str(out_path)]
    )

    classify_script.main()

    assert _classified_actions(out_path) == {MARKETING_SENDER: "trash"}
    assert _classified_actions(csv_path) == {MARKETING_SENDER: ""}


def test_main_keeps_the_header_of_an_empty_sender_csv_it_reports_nothing_for(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    senders_csv: Callable[..., Path],
    personal_senders_config: Callable[..., None],
) -> None:
    """Rewriting a header-only CSV in place must leave act.py and unsubscribe.py their columns."""
    personal_senders_config()
    csv_path = senders_csv()
    monkeypatch.setattr(sys, "argv", ["classify.py", "--csv", str(csv_path)])

    classify_script.main()

    assert "0 senders" in capsys.readouterr().out
    assert _header_columns(csv_path) == CSV_FIELDNAMES


@pytest.mark.parametrize(
    "argv_for",
    [
        pytest.param(lambda inside, outside: ["--csv", outside], id="the CSV it reads"),
        pytest.param(
            lambda inside, outside: ["--csv", inside, "--out", outside], id="the CSV it writes"
        ),
    ],
)
def test_main_refuses_a_path_outside_the_repository_directory(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    senders_csv: Callable[..., Path],
    argv_for: Callable[[str, str], list[str]],
) -> None:
    inside = str(senders_csv(MARKETING_SENDER))
    outside = str(tmp_path.parent / "elsewhere.csv")
    monkeypatch.setattr(sys, "argv", ["classify.py", *argv_for(inside, outside)])

    with pytest.raises(SystemExit, match="refusing path outside"):
        classify_script.main()
