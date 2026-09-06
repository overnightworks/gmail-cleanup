from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from classify import (
    PERSONAL_SENDERS_EXAMPLE_FILE,
    PersonalSendersConfig,
    classify,
    load_personal_senders_config,
)


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
                "personal_senders": ["a@example.com"],
                "trash_senders": ["b@example.com"],
                "personal_keep_patterns": [r"^a@example\.com$"],
            }
        ),
        encoding="utf-8",
    )

    assert load_personal_senders_config(path) == PersonalSendersConfig(
        personal_senders=("a@example.com",),
        trash_senders=("b@example.com",),
        personal_keep_patterns=(r"^a@example\.com$",),
    )


def test_load_personal_senders_config_raises_a_clear_error_when_missing(tmp_path: Path) -> None:
    missing_path = tmp_path / "personal_senders.json"

    with pytest.raises(FileNotFoundError, match=re.escape(str(missing_path))):
        load_personal_senders_config(missing_path)


@pytest.mark.parametrize(
    "content",
    [
        pytest.param("not json", id="not_json"),
        pytest.param(json.dumps({"personal_senders": []}), id="missing_field"),
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
    "field_overrides",
    [
        pytest.param({"trash_senders": "old@example.com"}, id="string_instead_of_list"),
        pytest.param({"personal_keep_patterns": {"a": "b"}}, id="dict_instead_of_list"),
        pytest.param({"personal_senders": ["ok@example.com", 3]}, id="non_string_element"),
    ],
)
def test_load_personal_senders_config_raises_a_clear_error_for_wrong_typed_fields(
    tmp_path: Path, field_overrides: dict[str, object]
) -> None:
    """A hand-edited field that isn't a list of strings is rejected, not silently misread.

    Regression guard: ``tuple("old@example.com")`` would otherwise split the
    string into single-character "senders", each trashed or kept in bulk.
    """
    path = tmp_path / "personal_senders.json"
    data: dict[str, object] = {
        "personal_senders": ["a@example.com"],
        "trash_senders": ["b@example.com"],
        "personal_keep_patterns": [r"^a@example\.com$"],
    }
    data.update(field_overrides)
    path.write_text(json.dumps(data), encoding="utf-8")

    with pytest.raises(ValueError, match=re.escape(str(path))):
        load_personal_senders_config(path)


def test_example_personal_senders_file_loads_as_a_valid_config() -> None:
    """The tracked example stays in sync with the loader's expected shape."""
    config = load_personal_senders_config(PERSONAL_SENDERS_EXAMPLE_FILE)

    assert config.personal_senders
    assert config.trash_senders
    assert config.personal_keep_patterns
