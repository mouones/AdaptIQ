"""Unit tests for the offline difficulty-recalibration math (roadmap item 2)."""

from scripts.recalibrate_question_difficulty import learned_difficulty


def test_harder_questions_get_higher_difficulty():
    # Lower observed correct-rate => harder => higher calibrated difficulty.
    easy = learned_difficulty(correct=90, total=100)
    medium = learned_difficulty(correct=50, total=100)
    hard = learned_difficulty(correct=10, total=100)
    assert easy < medium < hard


def test_balanced_rate_maps_to_mid_difficulty():
    assert learned_difficulty(correct=50, total=100) == 3.0


def test_output_is_clamped_to_scale():
    assert 1.0 <= learned_difficulty(correct=100, total=100) <= 5.0
    assert 1.0 <= learned_difficulty(correct=0, total=100) <= 5.0
    # Extremes clamp via the p-bound, not runaway logits.
    assert learned_difficulty(correct=0, total=100) == 5.0
    assert learned_difficulty(correct=100, total=100) == 1.0


def test_zero_total_is_neutral():
    assert learned_difficulty(correct=0, total=0) == 3.0
