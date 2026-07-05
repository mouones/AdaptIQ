"""
tests/test_elo.py — Unit tests for PvP Elo rating calculations.

Tests _compute_elo_change() directly to verify:
  - Standard Elo formula correctness
  - K-factor transition at 30 games
  - Win/loss/draw symmetry
  - Edge cases (equal ratings, extreme gaps)
"""

import uuid
import pytest

# Import the Elo function directly from pvp_service
from services.pvp_service import _compute_elo_change, ELO_K_NEW, ELO_K_REGULAR


def _make_ids():
    """Create two fresh UUIDs for testing."""
    return uuid.uuid4(), uuid.uuid4()


class TestEloBasics:
    """Core Elo formula validation."""

    def test_equal_elo_win_new_player(self):
        """New player (K=32) at equal Elo wins → +16.0 gain."""
        u1, u2 = _make_ids()
        delta = _compute_elo_change(1000.0, 1000.0, u1, u1, u2, total_matches=0)
        assert delta == 16.0

    def test_equal_elo_loss_new_player(self):
        """New player (K=32) at equal Elo loses → -16.0 loss."""
        u1, u2 = _make_ids()
        delta = _compute_elo_change(1000.0, 1000.0, u2, u1, u2, total_matches=0)
        assert delta == -16.0

    def test_equal_elo_draw(self):
        """Equal Elo draw → 0.0 change."""
        u1, u2 = _make_ids()
        delta = _compute_elo_change(1000.0, 1000.0, None, u1, u2, total_matches=0)
        assert delta == 0.0

    def test_higher_elo_wins_small_gain(self):
        """Stronger player wins → small gain (expected outcome)."""
        u1, u2 = _make_ids()
        delta = _compute_elo_change(1200.0, 1000.0, u1, u1, u2, total_matches=5)
        # Expected ≈ 0.76, so delta ≈ 32*(1-0.76) = 7.7
        assert 5.0 < delta < 12.0

    def test_lower_elo_wins_large_gain(self):
        """Weaker player wins → large gain (upset)."""
        u1, u2 = _make_ids()
        delta = _compute_elo_change(1000.0, 1200.0, u1, u1, u2, total_matches=5)
        # Expected ≈ 0.24, so delta ≈ 32*(1-0.24) = 24.3
        assert 20.0 < delta < 28.0


class TestKFactor:
    """K-factor transitions at 30 games."""

    def test_k_factor_new_player(self):
        """Under 30 games → K=32."""
        u1, u2 = _make_ids()
        delta_new = _compute_elo_change(1000.0, 1000.0, u1, u1, u2, total_matches=29)
        assert delta_new == 16.0  # K=32, equal Elo win → 32*0.5 = 16

    def test_k_factor_experienced_player(self):
        """At 30 games → K=16."""
        u1, u2 = _make_ids()
        delta_exp = _compute_elo_change(1000.0, 1000.0, u1, u1, u2, total_matches=30)
        assert delta_exp == 8.0  # K=16, equal Elo win → 16*0.5 = 8


class TestZeroSum:
    """Elo changes should be approximately zero-sum for equal K."""

    def test_zero_sum_equal_k_win(self):
        """Both new players: winner gain + loser loss = 0."""
        u1, u2 = _make_ids()
        delta1 = _compute_elo_change(1000.0, 1000.0, u1, u1, u2, total_matches=5)
        delta2 = _compute_elo_change(1000.0, 1000.0, u1, u2, u1, total_matches=5)
        assert abs(delta1 + delta2) < 0.01

    def test_zero_sum_unequal_elo(self):
        """Asymmetric Elo: changes still sum to ~0 with equal K."""
        u1, u2 = _make_ids()
        delta1 = _compute_elo_change(1200.0, 900.0, u1, u1, u2, total_matches=10)
        delta2 = _compute_elo_change(900.0, 1200.0, u1, u2, u1, total_matches=10)
        assert abs(delta1 + delta2) < 0.2

    def test_zero_sum_draw(self):
        """Draw at unequal Elo: higher-rated loses, lower-rated gains."""
        u1, u2 = _make_ids()
        # u1 is stronger (1200 vs 1000)
        delta1 = _compute_elo_change(1200.0, 1000.0, None, u1, u2, total_matches=5)
        delta2 = _compute_elo_change(1000.0, 1200.0, None, u2, u1, total_matches=5)
        # Higher rated should lose points in a draw
        assert delta1 < 0
        assert delta2 > 0
        assert abs(delta1 + delta2) < 0.2


class TestEdgeCases:
    """Edge cases for robustness."""

    def test_extreme_elo_gap_win(self):
        """800-point gap: underdog win gives near-max gain."""
        u1, u2 = _make_ids()
        delta = _compute_elo_change(200.0, 1000.0, u1, u1, u2, total_matches=0)
        # Expected ≈ 0.004, delta ≈ 32 * (1 - 0.004) ≈ 31.9
        assert 30.0 < delta <= 32.0

    def test_extreme_elo_gap_loss(self):
        """Massive underdog loses → near-zero loss."""
        u1, u2 = _make_ids()
        delta = _compute_elo_change(200.0, 1000.0, u2, u1, u2, total_matches=0)
        # Expected ≈ 0.004, delta ≈ 32 * (0 - 0.004) ≈ -0.1
        assert -2.0 < delta < 0.0

    def test_rounding_precision(self):
        """Result is always rounded to 1 decimal place."""
        u1, u2 = _make_ids()
        delta = _compute_elo_change(1050.0, 1000.0, u1, u1, u2, total_matches=15)
        assert delta == round(delta, 1)
