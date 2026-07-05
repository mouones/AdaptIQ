"""Regression tests for challenge pre-generation targeting."""

import routers.challenge as challenge_router_module


def test_challenge_pregen_levels_focus_current_and_neighbors(monkeypatch):
    monkeypatch.setattr(challenge_router_module, "CHALLENGE_PREGEN_LEVEL_RADIUS", 1)

    assert challenge_router_module._challenge_pregen_levels(3) == [3, 2, 4]
    assert challenge_router_module._challenge_pregen_levels(1) == [1, 2]
    assert challenge_router_module._challenge_pregen_levels(5) == [5, 4]


def test_challenge_pregen_levels_can_be_limited_to_current_level(monkeypatch):
    monkeypatch.setattr(challenge_router_module, "CHALLENGE_PREGEN_LEVEL_RADIUS", 0)

    assert challenge_router_module._challenge_pregen_levels(4) == [4]
