from __future__ import annotations

import sys
import urllib.error
from collections.abc import Callable
from datetime import date, timedelta
from pathlib import Path

import pytest

import unsubscribe

CANDIDATE_ROW = {
    "action": "unsubscribe",
    "from_email": "promo@example.com",
    "count": "5",
    "has_list_unsubscribe": "yes",
    "list_unsubscribe_url": "https://example.com/unsub",
    "list_unsubscribe_post": "One-Click",
    "newest_date": date.today().isoformat(),
}


@pytest.mark.parametrize(
    "overrides",
    [
        pytest.param({"action": "keep"}, id="kept sender is never unsubscribed"),
        pytest.param({"has_list_unsubscribe": "no"}, id="no List-Unsubscribe header"),
        pytest.param({"list_unsubscribe_post": ""}, id="no RFC 8058 one-click support"),
        pytest.param(
            {"list_unsubscribe_url": "mailto:unsub@example.com"}, id="no one-click http endpoint"
        ),
        pytest.param({"newest_date": "not-a-date"}, id="unparseable newest_date"),
        pytest.param(
            {"newest_date": (date.today() - timedelta(days=1000)).isoformat()},
            id="too old to still be active",
        ),
    ],
)
def test_load_candidates_excludes_ineligible_rows(
    tmp_path: Path,
    overrides: dict[str, str],
    write_csv_of_senders: Callable[[Path, list[dict[str, str]]], None],
) -> None:
    csv_path = tmp_path / "senders-full.csv"
    write_csv_of_senders(csv_path, [{**CANDIDATE_ROW, **overrides}])

    candidates = unsubscribe.load_candidates(csv_path, date.today() - timedelta(days=365))

    assert candidates == []


def test_load_candidates_includes_an_eligible_active_sender(
    tmp_path: Path, write_csv_of_senders: Callable[[Path, list[dict[str, str]]], None]
) -> None:
    csv_path = tmp_path / "senders-full.csv"
    write_csv_of_senders(csv_path, [CANDIDATE_ROW])

    candidates = unsubscribe.load_candidates(csv_path, date.today() - timedelta(days=365))

    assert [row["from_email"] for row in candidates] == ["promo@example.com"]


def test_unsubscribe_reports_success_for_a_2xx_response(
    monkeypatch: pytest.MonkeyPatch, fake_urlopen: Callable[[int], Callable[..., object]]
) -> None:
    monkeypatch.setattr(unsubscribe.urllib.request, "urlopen", fake_urlopen(200))

    assert unsubscribe.unsubscribe("https://example.com/unsub") == (True, "HTTP 200")


def test_unsubscribe_reports_failure_for_a_non_2xx_response(
    monkeypatch: pytest.MonkeyPatch, fake_urlopen: Callable[[int], Callable[..., object]]
) -> None:
    monkeypatch.setattr(unsubscribe.urllib.request, "urlopen", fake_urlopen(404))

    assert unsubscribe.unsubscribe("https://example.com/unsub") == (False, "HTTP 404")


def test_unsubscribe_reports_the_status_of_an_http_error(monkeypatch: pytest.MonkeyPatch) -> None:
    def raise_http_error(req: object, timeout: int) -> None:
        raise urllib.error.HTTPError("https://example.com/unsub", 429, "too many requests", None, None)

    monkeypatch.setattr(unsubscribe.urllib.request, "urlopen", raise_http_error)

    assert unsubscribe.unsubscribe("https://example.com/unsub") == (False, "HTTP 429")


def test_unsubscribe_reports_a_network_error(monkeypatch: pytest.MonkeyPatch) -> None:
    def raise_url_error(req: object, timeout: int) -> None:
        raise urllib.error.URLError("boom")

    monkeypatch.setattr(unsubscribe.urllib.request, "urlopen", raise_url_error)

    ok, detail = unsubscribe.unsubscribe("https://example.com/unsub")
    assert ok is False
    assert detail.startswith("network:")


def test_unsubscribe_reports_an_unexpected_error(monkeypatch: pytest.MonkeyPatch) -> None:
    def raise_value_error(req: object, timeout: int) -> None:
        raise ValueError("weird")

    monkeypatch.setattr(unsubscribe.urllib.request, "urlopen", raise_value_error)

    ok, detail = unsubscribe.unsubscribe("https://example.com/unsub")
    assert ok is False
    assert detail.startswith("unexpected:")


def test_main_dry_run_lists_candidates_without_sending_anything(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    write_csv_of_senders: Callable[[Path, list[dict[str, str]]], None],
) -> None:
    monkeypatch.setattr(unsubscribe, "SCRIPT_DIR", tmp_path)
    csv_path = tmp_path / "senders-full.csv"
    write_csv_of_senders(csv_path, [CANDIDATE_ROW])
    monkeypatch.setattr(sys, "argv", ["unsubscribe.py", "--csv", str(csv_path)])

    def fail_if_called(url: str) -> None:
        raise AssertionError("dry run must not send any unsubscribe request")

    monkeypatch.setattr(unsubscribe, "unsubscribe", fail_if_called)

    unsubscribe.main()

    out = capsys.readouterr().out
    assert "promo@example.com" in out
    assert "DRY RUN" in out


def test_main_execute_counts_successes_and_failures(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    write_csv_of_senders: Callable[[Path, list[dict[str, str]]], None],
) -> None:
    monkeypatch.setattr(unsubscribe, "SCRIPT_DIR", tmp_path)
    csv_path = tmp_path / "senders-full.csv"
    write_csv_of_senders(
        csv_path,
        [
            {**CANDIDATE_ROW, "from_email": "ok@example.com"},
            {
                **CANDIDATE_ROW,
                "from_email": "fail@example.com",
                "list_unsubscribe_url": "https://example.com/fail",
            },
        ],
    )
    monkeypatch.setattr(sys, "argv", ["unsubscribe.py", "--csv", str(csv_path), "--execute"])
    monkeypatch.setattr(
        unsubscribe,
        "unsubscribe",
        lambda url: (True, "HTTP 200") if "fail" not in url else (False, "HTTP 500"),
    )

    unsubscribe.main()

    out = capsys.readouterr().out
    assert "Done: 1 unsubscribed, 1 failed" in out


def test_main_refuses_a_csv_path_outside_the_script_directory(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    allowed = tmp_path / "scripts"
    allowed.mkdir()
    monkeypatch.setattr(unsubscribe, "SCRIPT_DIR", allowed)
    outside_csv = tmp_path / "elsewhere" / "senders-full.csv"
    monkeypatch.setattr(sys, "argv", ["unsubscribe.py", "--csv", str(outside_csv)])

    with pytest.raises(SystemExit, match="refusing path outside"):
        unsubscribe.main()
