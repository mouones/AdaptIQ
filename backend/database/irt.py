"""
database/irt.py — Item Response Theory calibration.

1-Parameter Logistic (1PL) IRT model:
    P(correct | θ, β) = 1 / (1 + exp(-(θ - β)))

where:
    θ (theta) = estimated user ability
    β (beta)  = question difficulty parameter

After each answer we:
    1. Estimate updated θ from user's response history
    2. Update β for the question via MLE approximation
    3. Map θ → difficulty scale (1-5) for next question selection

Also provides a lightweight in-memory tracker for per-session theta updates.
"""

from __future__ import annotations
import math
from typing import Optional


# ── Constants ────────────────────────────────────────────────────────────
THETA_INIT  = 0.0    # initial user ability (mid-scale)
BETA_INIT   = 0.0    # initial question difficulty (mid-scale)
LEARN_RATE  = 0.3    # gradient step size for online updates
THETA_RANGE = (-3.0, 3.0)   # clamp user ability to ±3 SD
BETA_RANGE  = (-3.0, 3.0)   # clamp question difficulty to ±3 SD

# Map IRT β → integer difficulty 1-5 for the React UI
_BETA_BREAKPOINTS = [-1.5, -0.5, 0.5, 1.5]   # thresholds between levels


# Compute probability of correct response for (theta, beta).
def irt_probability(theta: float, beta: float) -> float:
    """P(correct) under 1PL IRT model."""
    return 1.0 / (1.0 + math.exp(-(theta - beta)))


# Apply online gradient update to user ability theta.
def update_theta(theta: float, beta: float, correct: bool) -> float:
    """
    Online MLE update for user ability θ using gradient ascent on log-likelihood.
    
    ∂ logL/∂θ = (correct - P(correct)) * P(correct) * (1 - P(correct)) / P(correct)
              = correct - P(correct)   [simplified for 1PL]
    """
    p = irt_probability(theta, beta)
    gradient = (1 if correct else 0) - p
    new_theta = theta + LEARN_RATE * gradient
    return float(max(THETA_RANGE[0], min(THETA_RANGE[1], new_theta)))


# Apply online gradient update to question difficulty beta.
def update_beta(beta: float, theta: float, correct: bool) -> float:
    """
    Online MLE update for question difficulty β.
    If user got it right, question might be easier than estimated → lower β slightly.
    If user got it wrong, question might be harder → raise β slightly.
    """
    p = irt_probability(theta, beta)
    # Negative gradient (we want to maximise LL w.r.t. β, and
    # ∂ logL/∂β = -(correct - P) so we move β in the direction that
    # makes the observed outcome more likely)
    gradient = -(1 if correct else 0) + p
    new_beta = beta + LEARN_RATE * 0.5 * gradient   # slower beta updates
    return float(max(BETA_RANGE[0], min(BETA_RANGE[1], new_beta)))


# Convert continuous beta value to UI difficulty bucket 1-5.
def beta_to_difficulty(beta: float) -> int:
    """Map continuous IRT β ∈ [-3, 3] → integer difficulty 1-5."""
    for i, threshold in enumerate(_BETA_BREAKPOINTS):
        if beta < threshold:
            return i + 1
    return 5


# Convert UI difficulty bucket 1-5 to beta center value.
def difficulty_to_beta(difficulty: int) -> float:
    """Map integer difficulty 1-5 → IRT β centre point."""
    mapping = {1: -2.0, 2: -1.0, 3: 0.0, 4: 1.0, 5: 2.0}
    return mapping.get(difficulty, 0.0)


# Continuous variant of difficulty_to_beta for stored difficulty_irt values,
# which may be non-integer (e.g. the 2.5 column default). This is the linear
# form of difficulty_to_beta: 1→-2, 2→-1, 3→0, 4→1, 5→2, and 2.5→-0.5. Used by
# the ENABLE_IRT_LOGIT_SCALE path so the per-concept theta update receives a
# proper logit β instead of a raw 1-5 number.
def difficulty_to_beta_continuous(difficulty: float) -> float:
    """Map a continuous difficulty on the 1-5 scale → logit β, clamped to range."""
    beta = float(difficulty) - 3.0
    return max(BETA_RANGE[0], min(BETA_RANGE[1], beta))


# Select next difficulty using theta target plus smooth step constraints.
def next_difficulty(
    current_difficulty: int,
    answered_correct: bool,
    theta: float,
    recent_betas: list[float],
) -> int:
    """
    Choose next question difficulty using IRT-informed rule.
    
    - Uses θ to pick the difficulty level that maximises Fisher information:
      I(θ) = P(θ,β) * (1 - P(θ,β))  — maximised when P = 0.5, i.e. β ≈ θ
    - Clamps change to ±1 from current so difficulty ramps gradually
      (matching the React handleAnswer logic: min(prev+1,5) / max(prev-1,1))
    """
    # Ideal β = θ (zone of proximal development)
    ideal_beta = theta
    ideal_diff = beta_to_difficulty(ideal_beta)

    # Clamp to ±1 from current (matches React UI behaviour exactly)
    if answered_correct:
        candidate = min(current_difficulty + 1, 5)
    else:
        candidate = max(current_difficulty - 1, 1)

    # Blend: prefer IRT ideal but respect the ±1 clamp
    # If IRT agrees with the clamp, use it; otherwise prefer clamp
    if abs(ideal_diff - current_difficulty) <= 1:
        return ideal_diff
    return candidate


class UserAbilityTracker:
    """
    In-memory per-session θ tracker.
    Persisted to Redis as JSON for cross-request access.
    """

    # Initialize per-session theta tracker state.
    def __init__(self, theta: float = THETA_INIT):
        self.theta = theta
        self.response_count = 0

    # Record one response and update theta.
    def record(self, beta: float, correct: bool) -> float:
        """Update θ and return new value."""
        self.theta = update_theta(self.theta, beta, correct)
        self.response_count += 1
        return self.theta

    # Serialize tracker state for storage.
    def to_dict(self) -> dict:
        return {"theta": self.theta, "response_count": self.response_count}

    @classmethod
    # Reconstruct tracker state from serialized dictionary.
    def from_dict(cls, d: dict) -> "UserAbilityTracker":
        obj = cls(theta=d.get("theta", THETA_INIT))
        obj.response_count = d.get("response_count", 0)
        return obj


# Estimate theta by replaying a response history sequence.
def estimate_theta_from_history(
    responses: list[dict],  # list of {difficulty_sent, answered_correct}
) -> float:
    """
    Batch MLE estimate of θ from a list of past responses.
    Used by the IRT recalibration cron job.
    """
    theta = THETA_INIT
    for resp in responses:
        beta = difficulty_to_beta(resp["difficulty_sent"])
        correct = resp["answered_correct"]
        theta = update_theta(theta, beta, correct)
    return theta


# ── Zone of Proximal Development (ZPD) ───────────────────────────────────
# Target P(correct) range for optimal learning

ZPD_P_LOW = 0.60   # Lower bound (60% correct)
ZPD_P_HIGH = 0.75  # Upper bound (75% correct)


# Compute beta interval that targets zone-of-proximal-development accuracy.
def target_beta_range(theta: float) -> tuple[float, float]:
    """
    Find question difficulty range (β_low, β_high) for Zone of Proximal Development.

    We want to find β values such that:
    - P(correct | θ, β_low) = 0.60  (easier end of ZPD)
    - P(correct | θ, β_high) = 0.75 (harder end of ZPD)

    From 1PL: P = 1 / (1 + exp(-(θ - β)))
    Solving for β when P is known:
      exp(-(θ - β)) = (1 - P) / P
      -(θ - β) = ln((1 - P) / P)
      β = θ + ln((1 - P) / P)

    For P = 0.60: ln(0.40 / 0.60) = ln(2/3) ≈ -0.405
    For P = 0.75: ln(0.25 / 0.75) = ln(1/3) ≈ -1.099
    """
    # β_high: harder questions (target 60% correct)
    beta_high = theta - 0.405

    # β_low: easier questions (target 75% correct)
    beta_low = theta - 1.099

    # Clamp to valid range
    beta_low = max(BETA_RANGE[0], min(BETA_RANGE[1], beta_low))
    beta_high = max(BETA_RANGE[0], min(BETA_RANGE[1], beta_high))

    return (beta_low, beta_high)
