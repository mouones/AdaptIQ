"""Regression tests for test challenge streaks behavior."""

import pytest
from services.challenge_service import (
    check_streak_trigger, apply_level_change, 
    compute_rank_from_points, get_available_levels,
    calculate_points
)

def test_check_streak_trigger():
    # Level up: 4 correct
    res = check_streak_trigger(4, 0)
    assert res is not None
    assert res["direction"] == "up"
    
    # Level down: 2 wrong
    res = check_streak_trigger(0, 2)
    assert res is not None
    assert res["direction"] == "down"
    
    # No trigger
    assert check_streak_trigger(3, 1) is None

def test_apply_level_change_clamping():
    # Rank E only has levels 1, 2
    assert apply_level_change(2, "up", "E") == 2 # Max reached
    assert apply_level_change(1, "down", "E") == 1 # Min reached
    
    # Rank C has levels 2, 3, 4
    assert apply_level_change(3, "up", "C") == 4
    assert apply_level_change(4, "up", "C") == 4 # Max reached
    assert apply_level_change(2, "down", "C") == 2 # Min reached
    
    # Rank A has levels 1-5
    assert apply_level_change(4, "up", "A") == 5

def test_compute_rank_from_points():
    assert compute_rank_from_points(0) == "E"
    assert compute_rank_from_points(500) == "E"
    assert compute_rank_from_points(1000) == "D"
    assert compute_rank_from_points(3500) == "C"
    assert compute_rank_from_points(15000) == "A"
    assert compute_rank_from_points(20000) == "A"

def test_calculate_points():
    assert calculate_points(1, True) > 0
    assert calculate_points(1, False) < 0
    
    # Higher levels give more points
    assert calculate_points(5, True) > calculate_points(1, True)
    assert calculate_points(5, False) < calculate_points(1, False) # More negative
