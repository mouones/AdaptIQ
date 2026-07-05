"""
services/concept_irt.py

Per-Concept IRT - Tracks user ability per concept with confidence tracking.

Features:
  - Individual theta tracking per concept
  - Variance decay (uncertainty decreases per response)
  - Confidence tracking (MIN_RESPONSES_FOR_CONFIDENCE = 5)
  - Mastery level estimation

Provides per-concept ability updates, batch fetch helpers, and mastery mapping.
"""

from dataclasses import dataclass
from datetime import datetime, timezone
from sqlalchemy import select, update as sqlalchemy_update
from sqlalchemy.ext.asyncio import AsyncSession
from uuid import UUID

try:
    from database.concept_models import UserConceptTheta, Concept
    from database.irt import LEARN_RATE, THETA_RANGE, irt_probability
except ImportError:
    from .database.concept_models import UserConceptTheta, Concept
    from .database.irt import LEARN_RATE, THETA_RANGE, irt_probability


@dataclass
class ConceptThetaResult:
    """Result of theta query with confidence metadata.

    Attributes:
        theta: Current IRT theta (ability estimate) for the concept
        response_count: Number of responses recorded for this concept
        theta_variance: Uncertainty of the theta estimate (decreases over time)
        is_confident: True if we have enough responses to trust the theta value
    """

    theta: float
    response_count: int
    theta_variance: float
    is_confident: bool = False

    MIN_RESPONSES_FOR_CONFIDENCE = 5

    def __post_init__(self):
        """Auto-compute confidence based on response count."""
        self.is_confident = self.response_count >= self.MIN_RESPONSES_FOR_CONFIDENCE


class ConceptIRT:
    """Per-concept IRT theta tracking."""

    MIN_RESPONSES_FOR_CONFIDENCE = 5
    VARIANCE_DECAY_FACTOR = 0.95
    THETA_RANGE = (-3.0, 3.0)
    LEARN_RATE = 0.3  # Gradient step size

    @staticmethod
    def compute_update(
        theta: float, variance: float, beta: float, correct: bool
    ) -> tuple[float, float, str]:
        """Pure per-concept IRT update step (no persistence).

        Single source of truth for the theta/variance/mastery math so classic and
        custom rooms stay consistent (roadmap item 8). Returns
        (new_theta, new_variance, mastery_level).
        """
        p = irt_probability(theta, beta)
        gradient = (1 if correct else 0) - p
        new_theta = theta + ConceptIRT.LEARN_RATE * gradient
        new_theta = max(
            ConceptIRT.THETA_RANGE[0], min(ConceptIRT.THETA_RANGE[1], new_theta)
        )
        new_variance = variance * ConceptIRT.VARIANCE_DECAY_FACTOR
        return new_theta, new_variance, ConceptIRT._theta_to_mastery(new_theta)

    @staticmethod
    # Apply one IRT update step for a user-concept pair and persist it.
    async def update_concept_theta(
        db: AsyncSession,
        user_id: UUID,
        concept_id: UUID,
        beta: float,
        correct: bool,
    ) -> float:
        """
        Update theta for user in a specific concept using IRT 1PL model.

        Algorithm:
        1. Get or create theta record for (user, concept)
        2. Calculate IRT probability for this response
        3. Update theta: Î´Î¸ = Î± * (response - P(correct))
        4. Decay variance (uncertainty decreases) 5. Save to database

        Args:
            db: AsyncSession
            user_id: User UUID
            concept_id: Concept UUID
            beta: Question difficulty
            correct: Whether answer was correct

        Returns:
            New theta value (clamped to THETA_RANGE)
        """
        # Get or create theta record
        now = datetime.now(timezone.utc).replace(tzinfo=None)

        stmt = select(UserConceptTheta).where(
            (UserConceptTheta.user_id == user_id)
            & (UserConceptTheta.concept_id == concept_id)
        )
        theta_record = (await db.execute(stmt)).scalar_one_or_none()

        if not theta_record:
            # Create new record
            theta_record = UserConceptTheta(
                user_id=user_id,
                concept_id=concept_id,
                theta=0.0,
                theta_variance=1.0,
                response_count=0,
                exposure_count=0,
                first_seen_at=now,
            )
            db.add(theta_record)
            await db.flush()

        # IRT update via the shared pure helper (single source of truth).
        new_theta, new_variance, mastery_level = ConceptIRT.compute_update(
            theta_record.theta, theta_record.theta_variance, beta, correct
        )

        # Update record
        stmt = (
            sqlalchemy_update(UserConceptTheta)
            .where(UserConceptTheta.id == theta_record.id)
            .values(
                theta=new_theta,
                theta_variance=new_variance,
                response_count=UserConceptTheta.response_count + 1,
                exposure_count=UserConceptTheta.exposure_count + 1,
                mastery_level=mastery_level,
                last_played_at=now,
                last_updated=now,
            )
        )
        await db.execute(stmt)
        await db.commit()

        return float(new_theta)

    @staticmethod
    # Return current concept theta, defaulting to neutral when absent.
    async def get_concept_theta(
        db: AsyncSession, user_id: UUID, concept_id: UUID
    ) -> float:
        """Get theta for user in concept, default 0.0 if not found."""
        stmt = select(UserConceptTheta).where(
            (UserConceptTheta.user_id == user_id)
            & (UserConceptTheta.concept_id == concept_id)
        )
        record = (await db.execute(stmt)).scalar_one_or_none()

        if not record:
            return 0.0

        return float(record.theta)

    @staticmethod
    # Return concept theta plus confidence metadata for decision logic.
    async def get_concept_theta_with_confidence(
        db: AsyncSession, user_id: UUID, concept_id: UUID
    ) -> ConceptThetaResult:
        """Get theta with confidence metadata."""
        stmt = select(UserConceptTheta).where(
            (UserConceptTheta.user_id == user_id)
            & (UserConceptTheta.concept_id == concept_id)
        )
        record = (await db.execute(stmt)).scalar_one_or_none()

        if not record:
            return ConceptThetaResult(
                theta=0.0,
                response_count=0,
                theta_variance=1.0,
                is_confident=False,
            )

        is_confident = (
            record.response_count
            >= ConceptIRT.MIN_RESPONSES_FOR_CONFIDENCE
        )
        return ConceptThetaResult(
            theta=float(record.theta),
            response_count=record.response_count,
            theta_variance=float(record.theta_variance),
            is_confident=is_confident,
        )

    @staticmethod
    # Fetch theta values for many concepts and fill missing ones with neutral.
    async def get_user_concept_thetas(
        db: AsyncSession, user_id: UUID, concept_ids: list[UUID]
    ) -> dict[str, float]:
        """Get theta values for multiple concepts (for session initialization)."""
        stmt = select(UserConceptTheta).where(
            (UserConceptTheta.user_id == user_id)
            & (UserConceptTheta.concept_id.in_(concept_ids))
        )
        records = (await db.execute(stmt)).scalars().all()

        theta_map = {}
        for record in records:
            theta_map[str(record.concept_id)] = float(record.theta)

        # Fill in missing concepts with 0.0
        for cid in concept_ids:
            if str(cid) not in theta_map:
                theta_map[str(cid)] = 0.0

        return theta_map

    @staticmethod
    # Convert theta into a discrete mastery label.
    def _theta_to_mastery(theta: float) -> str:
        """Map theta to mastery level."""
        if theta < -1.0:
            return "BEGINNER"
        elif theta < 0.0:
            return "NOVICE"
        elif theta < 1.0:
            return "INTERMEDIATE"
        elif theta < 2.0:
            return "ADVANCED"
        else:
            return "EXPERT"

