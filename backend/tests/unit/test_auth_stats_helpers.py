"""Regression tests for test auth stats helpers behavior."""

from datetime import date

from routers.auth import _compute_room_progress, _compute_streak_days


def test_compute_streak_days_counts_consecutive_activity() -> None:
    today = date(2026, 4, 18)
    day_counts = {
        date(2026, 4, 18): 4,
        date(2026, 4, 17): 3,
        date(2026, 4, 16): 1,
        date(2026, 4, 15): 0,
    }

    assert _compute_streak_days(day_counts, today) == 3


def test_compute_streak_days_is_zero_when_today_has_no_activity() -> None:
    today = date(2026, 4, 18)
    day_counts = {
        date(2026, 4, 17): 2,
        date(2026, 4, 16): 5,
    }

    assert _compute_streak_days(day_counts, today) == 0


def test_compute_room_progress_returns_zeros_for_empty_counts() -> None:
    progress = _compute_room_progress(0, 0, 0, 0)

    assert progress.classic == 0
    assert progress.challenge == 0
    assert progress.custom == 0
    assert progress.pvp == 0
    assert progress.visual == 0


def test_compute_room_progress_distributes_share_percentages() -> None:
    progress = _compute_room_progress(5, 3, 2, 0)

    assert progress.classic == 50
    assert progress.challenge == 30
    assert progress.custom == 20
    assert progress.pvp == 0
    assert progress.visual == 0
