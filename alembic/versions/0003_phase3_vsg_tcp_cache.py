"""Phase 3 VSG, TCP, Provenance, and Result Cache persistence tables (VSG-1, VSG-6, TCP-5, CACHE-1).

Revision ID: 0003
Revises: 0002
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "vsg_states",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("dataset_id", sa.Integer, sa.ForeignKey("datasets.id", ondelete="CASCADE"), nullable=True),
        sa.Column("dataset_sha256", sa.String(64)),
        sa.Column("vsg_sha256", sa.String(64), nullable=False),
        sa.Column("auto_log_hash", sa.String(64)),
        sa.Column("accepted", sa.Boolean, nullable=False, server_default=sa.false()),
        sa.Column("targets_json", sa.Text),
        sa.Column("state_json", sa.Text, nullable=False),
        sa.Column("payload_sha256", sa.String(64), nullable=False),
        sa.Column("created_at", sa.DateTime),
        sa.Column("updated_at", sa.DateTime),
    )
    op.create_index("ix_vsg_states_dataset_id", "vsg_states", ["dataset_id"])

    op.create_table(
        "tcp_configs",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("dataset_id", sa.Integer, sa.ForeignKey("datasets.id", ondelete="CASCADE"), nullable=True),
        sa.Column("vsg_id", sa.Integer, sa.ForeignKey("vsg_states.id", ondelete="CASCADE"), nullable=True),
        sa.Column("tcp_sha256", sa.String(64), nullable=False),
        sa.Column("preregistration_sha256", sa.String(64)),
        sa.Column("accepted", sa.Boolean, nullable=False, server_default=sa.false()),
        sa.Column("locked", sa.Boolean, nullable=False, server_default=sa.false()),
        sa.Column("config_json", sa.Text, nullable=False),
        sa.Column("characterization_json", sa.Text),
        sa.Column("payload_sha256", sa.String(64), nullable=False),
        sa.Column("created_at", sa.DateTime),
        sa.Column("updated_at", sa.DateTime),
    )
    op.create_index("ix_tcp_configs_dataset_id", "tcp_configs", ["dataset_id"])
    op.create_index("ix_tcp_configs_vsg_id", "tcp_configs", ["vsg_id"])

    op.create_table(
        "provenance_records",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("dataset_id", sa.Integer, sa.ForeignKey("datasets.id", ondelete="CASCADE"), nullable=True),
        sa.Column("run_id", sa.Integer, nullable=True),
        sa.Column("seq", sa.Integer, nullable=False),
        sa.Column("entity_type", sa.String(80), nullable=False),
        sa.Column("entity_id", sa.String(200), nullable=False),
        sa.Column("target", sa.String(200)),
        sa.Column("source", sa.String(60), nullable=False),
        sa.Column("phase", sa.String(20), nullable=False),
        sa.Column("dataset_sha256", sa.String(64)),
        sa.Column("previous_record_sha256", sa.String(64)),
        sa.Column("record_sha256", sa.String(64), nullable=False),
        sa.Column("payload_json", sa.Text, nullable=False),
        sa.Column("created_at", sa.DateTime),
    )
    op.create_index("ix_provenance_records_dataset_id", "provenance_records", ["dataset_id"])
    op.create_index("ix_provenance_records_run_id", "provenance_records", ["run_id"])

    op.create_table(
        "result_cache_entries",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("cache_key_sha256", sa.String(64), nullable=False, unique=True),
        sa.Column("data_hash", sa.String(64)),
        sa.Column("variable_set_json", sa.Text, nullable=False),
        sa.Column("test_id", sa.String(40), nullable=False),
        sa.Column("parameters_json", sa.Text, nullable=False),
        sa.Column("seed", sa.Integer, nullable=False),
        sa.Column("library_versions_json", sa.Text, nullable=False),
        sa.Column("artifact_json", sa.Text, nullable=False),
        sa.Column("artifact_sha256", sa.String(64), nullable=False),
        sa.Column("created_at", sa.DateTime),
    )
    op.create_index("ix_result_cache_entries_cache_key_sha256", "result_cache_entries", ["cache_key_sha256"], unique=True)
    op.create_index("ix_result_cache_entries_data_hash", "result_cache_entries", ["data_hash"])
    op.create_index("ix_result_cache_entries_test_id", "result_cache_entries", ["test_id"])


def downgrade() -> None:
    op.drop_index("ix_result_cache_entries_test_id", table_name="result_cache_entries")
    op.drop_index("ix_result_cache_entries_data_hash", table_name="result_cache_entries")
    op.drop_index("ix_result_cache_entries_cache_key_sha256", table_name="result_cache_entries")
    op.drop_table("result_cache_entries")

    op.drop_index("ix_provenance_records_run_id", table_name="provenance_records")
    op.drop_index("ix_provenance_records_dataset_id", table_name="provenance_records")
    op.drop_table("provenance_records")

    op.drop_index("ix_tcp_configs_vsg_id", table_name="tcp_configs")
    op.drop_index("ix_tcp_configs_dataset_id", table_name="tcp_configs")
    op.drop_table("tcp_configs")

    op.drop_index("ix_vsg_states_dataset_id", table_name="vsg_states")
    op.drop_table("vsg_states")
