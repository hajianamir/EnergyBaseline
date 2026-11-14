"""Use neutral storage terminology for the encrypted connection payload.

Revision ID: 0004
Revises: 0003
"""
from alembic import op
import sqlalchemy as sa

revision = "0004"
down_revision = "0003"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.alter_column("connections", "secret_blob", new_column_name="protected_payload", existing_type=sa.Text())


def downgrade() -> None:
    op.alter_column("connections", "protected_payload", new_column_name="secret_blob", existing_type=sa.Text())
