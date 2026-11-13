"""Phase 2 dataset schema and non-routine events persistence tables (DAT-5, DAT-8).

Revision ID: 0002
Revises: 0001
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "dataset_schemas",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("dataset_id", sa.Integer, sa.ForeignKey("datasets.id", ondelete="CASCADE"), nullable=True),
        sa.Column("name", sa.String(300)),
        sa.Column("schema_version", sa.String(20)),
        sa.Column("dataset_sha256", sa.String(64)),
        sa.Column("schema_sha256", sa.String(64), nullable=False),
        sa.Column("time_column", sa.String(200), nullable=False),
        sa.Column("targets_json", sa.Text),
        sa.Column("outlier_policy", sa.String(30)),
        sa.Column("schema_json", sa.Text, nullable=False),
        sa.Column("ingestion_summary_json", sa.Text),
        sa.Column("created_at", sa.DateTime),
        sa.Column("updated_at", sa.DateTime),
    )
    op.create_index("ix_dataset_schemas_dataset_id", "dataset_schemas", ["dataset_id"])

    op.create_table(
        "non_routine_events",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("dataset_id", sa.Integer, sa.ForeignKey("datasets.id", ondelete="CASCADE"), nullable=True),
        sa.Column("schema_id", sa.Integer, sa.ForeignKey("dataset_schemas.id", ondelete="CASCADE"), nullable=True),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column("start_ts", sa.String(64), nullable=False),
        sa.Column("end_ts", sa.String(64), nullable=False),
        sa.Column("action", sa.String(30)),
        sa.Column("affected_targets_json", sa.Text),
        sa.Column("reason", sa.Text),
        sa.Column("created_at", sa.DateTime),
    )
    op.create_index("ix_non_routine_events_dataset_id", "non_routine_events", ["dataset_id"])
    op.create_index("ix_non_routine_events_schema_id", "non_routine_events", ["schema_id"])


def downgrade() -> None:
    op.drop_index("ix_non_routine_events_schema_id", table_name="non_routine_events")
    op.drop_index("ix_non_routine_events_dataset_id", table_name="non_routine_events")
    op.drop_table("non_routine_events")
    op.drop_index("ix_dataset_schemas_dataset_id", table_name="dataset_schemas")
    op.drop_table("dataset_schemas")
