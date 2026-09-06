from __future__ import annotations

from classify import classify


def test_list_unsubscribe_header_trashes_an_unlisted_sender() -> None:
    """An unlisted sender with a List-Unsubscribe header is treated as marketing."""
    assert classify("updates@some-random-service.example.com", has_unsub="yes") == "trash"


def test_keep_pattern_wins_over_list_unsubscribe() -> None:
    """A keep-listed sender stays kept even when it also sends List-Unsubscribe."""
    assert classify("noreply@github.com", has_unsub="yes") == "keep"
