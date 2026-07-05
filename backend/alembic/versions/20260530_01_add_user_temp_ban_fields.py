"""Add temporary ban fields to users

Revision ID: 20260530_01
Revises: bb15d1154671
Create Date: 2026-05-30

Adds ban_until and ban_reason so admins can apply timed account bans.
"""

from alembic import op
import sqlalchemy as sa

revision = "20260530_01"
down_revision = "bb15d1154671"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("users", sa.Column("ban_until", sa.DateTime(), nullable=True))
    op.add_column("users", sa.Column("ban_reason", sa.Text(), nullable=True))
    op.create_index("ix_users_ban_until", "users", ["ban_until"], unique=False)


def downgrade() -> None:
    op.drop_index("ix_users_ban_until", table_name="users")
    op.drop_column("users", "ban_reason")
    op.drop_column("users", "ban_until")
