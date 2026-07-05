"""Regression tests for test irt engine behavior."""

import pytest
from database.irt import (
    irt_probability, update_theta, update_beta, 
    beta_to_difficulty, difficulty_to_beta,
    next_difficulty, target_beta_range,
    THETA_RANGE, BETA_RANGE
)

def test_irt_probability():
    # Equal ability and difficulty -> 50% chance
    assert round(irt_probability(0.0, 0.0), 2) == 0.50
    # High ability, low difficulty -> high chance
    assert irt_probability(2.0, -1.0) > 0.90
    # Low ability, high difficulty -> low chance
    assert irt_probability(-2.0, 1.0) < 0.10

def test_update_theta():
    theta = 0.0
    beta = 0.0
    
    # Correct answer -> theta increases
    new_theta_correct = update_theta(theta, beta, True)
    assert new_theta_correct > theta
    
    # Wrong answer -> theta decreases
    new_theta_wrong = update_theta(theta, beta, False)
    assert new_theta_wrong < theta

def test_update_theta_clamping():
    # Assuming theta is very high, it shouldn't exceed THETA_RANGE
    new_theta = update_theta(THETA_RANGE[1], -3.0, True)
    assert new_theta <= THETA_RANGE[1]
    
    new_theta_low = update_theta(THETA_RANGE[0], 3.0, False)
    assert new_theta_low >= THETA_RANGE[0]

def test_target_beta_range():
    theta = 1.0
    beta_low, beta_high = target_beta_range(theta)
    
    # beta_low is for easier questions (target 75% correct)
    p_low = irt_probability(theta, beta_low)
    assert round(p_low, 2) == 0.75
    
    # beta_high is for harder questions (target 60% correct)
    p_high = irt_probability(theta, beta_high)
    assert round(p_high, 2) == 0.60
    
def test_next_difficulty_clamps():
    # If user gets it wrong at level 1, they stay at level 1
    assert next_difficulty(1, False, -3.0, []) == 1
    
    # If user gets it right at level 5, they stay at level 5
    assert next_difficulty(5, True, 3.0, []) == 5
    
    # Normal progression
    assert next_difficulty(2, True, 0.0, []) in (2, 3)
