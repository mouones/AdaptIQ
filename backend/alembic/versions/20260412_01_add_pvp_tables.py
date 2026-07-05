"""add pvp tables

Revision ID: 20260412_01
Revises: 20260411_03_add_question_caching_columns
Create Date: 2026-04-12

Creates:
  - pvp_matchmaking_queue: Players waiting for a match
  - pvp_matches: Active/completed 1v1 matches
  - pvp_match_answers: Per-question answer records
  - pvp_ratings: Elo ratings and match stats
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision = '20260412_01_add_pvp'
down_revision = '20260411_03'
branch_labels = None
depends_on = None


def upgrade() -> None:
    # PvP Matchmaking Queue
    op.create_table(
        'pvp_matchmaking_queue',
        sa.Column('id', postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column('user_id', postgresql.UUID(as_uuid=True), sa.ForeignKey('users.id'), nullable=False),
        sa.Column('topic', sa.String(50), nullable=False, server_default='Mixed'),
        sa.Column('elo_rating', sa.Float, nullable=False, server_default='1000.0'),
        sa.Column('concepts_json', sa.Text, nullable=True),
        sa.Column('joined_at', sa.DateTime, server_default=sa.func.now()),
        sa.Column('status', sa.String(20), server_default='waiting'),
    )
    op.create_index('ix_pvp_queue_user_id', 'pvp_matchmaking_queue', ['user_id'])

    # PvP Matches
    op.create_table(
        'pvp_matches',
        sa.Column('id', postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column('user1_id', postgresql.UUID(as_uuid=True), sa.ForeignKey('users.id'), nullable=False),
        sa.Column('user2_id', postgresql.UUID(as_uuid=True), sa.ForeignKey('users.id'), nullable=False),
        sa.Column('topic', sa.String(50), nullable=False),
        sa.Column('status', sa.String(20), server_default='active'),
        sa.Column('total_questions', sa.Integer, server_default='5'),
        sa.Column('questions_json', sa.Text, nullable=True),
        sa.Column('user1_score', sa.Integer, server_default='0'),
        sa.Column('user2_score', sa.Integer, server_default='0'),
        sa.Column('user1_finished', sa.Boolean, server_default='false'),
        sa.Column('user2_finished', sa.Boolean, server_default='false'),
        sa.Column('winner_id', postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column('elo_change', sa.Float, server_default='0.0'),
        sa.Column('started_at', sa.DateTime, server_default=sa.func.now()),
        sa.Column('ended_at', sa.DateTime, nullable=True),
        sa.Column('created_at', sa.DateTime, server_default=sa.func.now()),
    )
    op.create_index('ix_pvp_matches_user1', 'pvp_matches', ['user1_id'])
    op.create_index('ix_pvp_matches_user2', 'pvp_matches', ['user2_id'])

    # PvP Match Answers
    op.create_table(
        'pvp_match_answers',
        sa.Column('id', postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column('match_id', postgresql.UUID(as_uuid=True), sa.ForeignKey('pvp_matches.id'), nullable=False),
        sa.Column('user_id', postgresql.UUID(as_uuid=True), sa.ForeignKey('users.id'), nullable=False),
        sa.Column('question_id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('question_index', sa.Integer, nullable=False),
        sa.Column('chosen_answer', sa.String(500), nullable=False),
        sa.Column('is_correct', sa.Boolean, nullable=False),
        sa.Column('time_taken', sa.Float, nullable=True),
        sa.Column('answered_at', sa.DateTime, server_default=sa.func.now()),
    )
    op.create_index('ix_pvp_answers_match', 'pvp_match_answers', ['match_id'])

    # PvP Ratings
    op.create_table(
        'pvp_ratings',
        sa.Column('id', postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column('user_id', postgresql.UUID(as_uuid=True), sa.ForeignKey('users.id'), nullable=False, unique=True),
        sa.Column('elo_rating', sa.Float, server_default='1000.0'),
        sa.Column('total_matches', sa.Integer, server_default='0'),
        sa.Column('total_wins', sa.Integer, server_default='0'),
        sa.Column('total_losses', sa.Integer, server_default='0'),
        sa.Column('total_draws', sa.Integer, server_default='0'),
        sa.Column('win_streak', sa.Integer, server_default='0'),
        sa.Column('best_streak', sa.Integer, server_default='0'),
        sa.Column('updated_at', sa.DateTime, server_default=sa.func.now()),
    )
    op.create_index('ix_pvp_ratings_user', 'pvp_ratings', ['user_id'])


def downgrade() -> None:
    op.drop_table('pvp_match_answers')
    op.drop_table('pvp_matches')
    op.drop_table('pvp_matchmaking_queue')
    op.drop_table('pvp_ratings')
