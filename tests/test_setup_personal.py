from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest

import classify
import setup_personal

PERSONAL_LABEL_ID = "label-personal"
EXISTING_PERSONAL_LABEL = {"id": PERSONAL_LABEL_ID, "name": setup_personal.PERSONAL_LABEL}
LABELING_CHANGE = {"addLabelIds": [PERSONAL_LABEL_ID]}

FRIEND = "friend@example.com"
ACQUAINTANCE = "acquaintance@example.com"


def test_an_existing_personal_label_is_reused_rather_than_created_again(
    fake_gmail_service_builder: Callable[..., Any],
) -> None:
    service = fake_gmail_service_builder({}, existing_labels=[EXISTING_PERSONAL_LABEL])

    label_id = setup_personal.ensure_label(service, setup_personal.PERSONAL_LABEL)

    assert label_id == PERSONAL_LABEL_ID
    assert service.label_create_calls == []


def test_a_missing_personal_label_is_created_visible_in_both_lists(
    fake_gmail_service_builder: Callable[..., Any],
) -> None:
    service = fake_gmail_service_builder({}, existing_labels=[{"id": "l1", "name": "Other"}])

    label_id = setup_personal.ensure_label(service, setup_personal.PERSONAL_LABEL)

    assert service.label_create_calls == [
        {
            "name": setup_personal.PERSONAL_LABEL,
            "labelListVisibility": "labelShow",
            "messageListVisibility": "show",
        }
    ]
    assert service.created_label_ids == [label_id]


def _label_sender(service: Any, sender: str) -> int:
    return setup_personal.apply_label_to_existing(service, sender, PERSONAL_LABEL_ID)


BULK_ACTIONS = [
    pytest.param(_label_sender, id="labeling"),
    pytest.param(setup_personal.trash_from, id="trashing"),
]


@pytest.fixture
def change_applied_by(
    trash_label_change: dict[str, Any],
) -> dict[Callable[[Any, str], int], dict[str, Any]]:
    """The label change each bulk action applies to every message it finds."""
    return {_label_sender: LABELING_CHANGE, setup_personal.trash_from: trash_label_change}


@pytest.mark.parametrize("bulk_action", BULK_ACTIONS)
def test_a_bulk_action_walks_every_page_of_the_senders_mail(
    fake_gmail_service_builder: Callable[..., Any],
    build_messages_page: Callable[..., dict[str, Any]],
    change_applied_by: dict[Callable[[Any, str], int], dict[str, Any]],
    bulk_action: Callable[[Any, str], int],
) -> None:
    service = fake_gmail_service_builder(
        {
            f"from:{FRIEND}": [
                build_messages_page("m1", "m2", next_page_token="1"),
                build_messages_page("m3"),
            ]
        }
    )

    assert bulk_action(service, FRIEND) == 3
    expected_change = change_applied_by[bulk_action]
    assert service.batch_modify_calls == [
        {"ids": ["m1", "m2"], **expected_change},
        {"ids": ["m3"], **expected_change},
    ]


@pytest.mark.parametrize("bulk_action", BULK_ACTIONS)
def test_a_bulk_action_touches_nothing_for_a_sender_without_mail(
    fake_gmail_service_builder: Callable[..., Any],
    build_messages_page: Callable[..., dict[str, Any]],
    bulk_action: Callable[[Any, str], int],
) -> None:
    service = fake_gmail_service_builder({f"from:{FRIEND}": [build_messages_page()]})

    assert bulk_action(service, FRIEND) == 0
    assert service.batch_modify_calls == []


def test_the_filter_matches_every_personal_sender(
    fake_gmail_service_builder: Callable[..., Any],
) -> None:
    service = fake_gmail_service_builder({})

    filter_id = setup_personal.create_filter(service, (FRIEND, ACQUAINTANCE), PERSONAL_LABEL_ID)

    assert service.filter_create_calls == [
        {"criteria": {"from": f"{FRIEND} OR {ACQUAINTANCE}"}, "action": LABELING_CHANGE}
    ]
    assert service.created_filter_ids == [filter_id]


def test_main_labels_personal_mail_filters_future_mail_and_trashes_the_rest(
    capsys: pytest.CaptureFixture[str],
    fake_gmail_service_builder: Callable[..., Any],
    gmail_service_behind: Callable[..., None],
    personal_senders_config: Callable[..., None],
    build_messages_page: Callable[..., dict[str, Any]],
    build_trash_body: Callable[..., dict[str, Any]],
) -> None:
    personal_senders_config(personal_senders=[FRIEND], trash_senders=[ACQUAINTANCE])
    service = fake_gmail_service_builder(
        {
            f"from:{FRIEND}": [build_messages_page("m1", "m2")],
            f"from:{ACQUAINTANCE}": [build_messages_page("m3")],
        },
        existing_labels=[EXISTING_PERSONAL_LABEL],
    )
    gmail_service_behind(setup_personal, service)

    setup_personal.main()

    assert service.batch_modify_calls == [
        {"ids": ["m1", "m2"], **LABELING_CHANGE},
        build_trash_body("m3"),
    ]
    assert service.filter_create_calls == [
        {"criteria": {"from": FRIEND}, "action": LABELING_CHANGE}
    ]
    out = capsys.readouterr().out
    assert "Total existing messages labeled: 2" in out
    assert "Total trashed: 1" in out


def test_main_creates_no_filter_when_no_personal_sender_is_configured(
    capsys: pytest.CaptureFixture[str],
    fake_gmail_service_builder: Callable[..., Any],
    gmail_service_behind: Callable[..., None],
    personal_senders_config: Callable[..., None],
) -> None:
    """A filter with an empty ``from`` would match every incoming message."""
    personal_senders_config()
    service = fake_gmail_service_builder({}, existing_labels=[EXISTING_PERSONAL_LABEL])
    gmail_service_behind(setup_personal, service)

    setup_personal.main()

    assert service.filter_create_calls == []
    assert "No personal_senders configured" in capsys.readouterr().out


def test_main_raises_the_loaders_missing_file_error_before_requesting_credentials(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """main() surfaces the config loader's error and never reaches the Gmail OAuth flow."""
    missing_path = tmp_path / "personal_senders.json"
    monkeypatch.setattr(classify, "PERSONAL_SENDERS_FILE", missing_path)

    def fail_if_called(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("get_credentials must not run before the config loads")

    monkeypatch.setattr(setup_personal, "get_credentials", fail_if_called)

    with pytest.raises(FileNotFoundError, match=str(missing_path)):
        setup_personal.main()
