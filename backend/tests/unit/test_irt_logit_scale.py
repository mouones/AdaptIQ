"""Unit tests for the ENABLE_IRT_LOGIT_SCALE theta/beta unification (roadmap item 1).

These lock in the pure math behind the flag: the continuous difficulty->beta
converter used by the per-concept theta update, and the ZPD-band -> 1-5 bucket
conversion used by classic question selection. The flag itself is default-off;
these tests exercise the conversion helpers the flag path relies on.
"""

from database.irt import (
    beta_to_difficulty,
    difficulty_to_beta,
    difficulty_to_beta_continuous,
    irt_probability,
    target_beta_range,
)


def test_difficulty_to_beta_continuous_matches_integer_mapping():
    # Continuous converter agrees with the integer mapping on the 1-5 grid.
    for difficulty in (1, 2, 3, 4, 5):
        assert difficulty_to_beta_continuous(difficulty) == difficulty_to_beta(difficulty)


def test_difficulty_to_beta_continuous_handles_fractional_default():
    # The difficulty_irt column default is 2.5; it must map to a sensible mid-low beta.
    assert difficulty_to_beta_continuous(2.5) == -0.5


def test_difficulty_to_beta_continuous_clamps_to_range():
    assert difficulty_to_beta_continuous(-10) == -3.0
    assert difficulty_to_beta_continuous(99) == 3.0


def test_zpd_band_converts_to_nonempty_bucket_range():
    # The core bug the flag fixes: the logit ZPD band, converted to 1-5 buckets,
    # yields a valid non-empty range that scales with ability. Before conversion
    # the raw logit band (e.g. [-1.1, -0.4]) matched no rows in the 1-5 column.
    for theta, expected in [(0.0, (2, 3)), (1.5, (3, 4)), (-1.5, (1, 1)), (2.5, (4, 5))]:
        beta_low, beta_high = target_beta_range(theta)
        low = beta_to_difficulty(beta_low)
        high = beta_to_difficulty(beta_high)
        assert 1 <= low <= high <= 5
        assert (low, high) == expected


def test_logit_update_probability_is_reasonable_when_scaled():
    # With the continuous converter, a medium question (difficulty 3 -> beta 0)
    # against a neutral learner (theta 0) yields ~0.5, i.e. an informative item,
    # instead of the near-0 probability produced by feeding a raw 1-5 value.
    beta = difficulty_to_beta_continuous(3)
    p = irt_probability(0.0, beta)
    assert 0.4 < p < 0.6
