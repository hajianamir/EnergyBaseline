"""initial schema

Revision ID: 0001
Revises:
"""
from alembic import op
import sqlalchemy as sa

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "health",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("ts", sa.DateTime),
        sa.Column("note", sa.String(200)),
    )
    op.create_table(
        "datasets",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("name", sa.String(300)),
        sa.Column("source_path", sa.Text),
        sa.Column("sha256", sa.String(64)),
        sa.Column("n_rows", sa.Integer),
        sa.Column("n_cols", sa.Integer),
        sa.Column("schema_json", sa.Text),
        sa.Column("imported_at", sa.DateTime),
    )
    op.create_table(
        "runs",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("dataset_id", sa.Integer, sa.ForeignKey("datasets.id"), nullable=True),
        sa.Column("started_at", sa.DateTime),
        sa.Column("finished_at", sa.DateTime, nullable=True),
        sa.Column("status", sa.String(30)),
        sa.Column("seed", sa.Integer),
        sa.Column("targets_json", sa.Text),
        sa.Column("config_json", sa.Text),
        sa.Column("versions_json", sa.Text),
        sa.Column("summary_json", sa.Text),
        sa.Column("results_path", sa.Text),
    )
    op.create_table(
        "auto_resolutions",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("run_id", sa.Integer, sa.ForeignKey("runs.id")),
        sa.Column("target", sa.String(200)),
        sa.Column("setting", sa.String(100)),
        sa.Column("mode", sa.String(20)),
        sa.Column("value", sa.Text),
        sa.Column("signals", sa.Text),
        sa.Column("rule", sa.Text),
        sa.Column("created_at", sa.DateTime),
    )
    op.create_table(
        "model_results",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("run_id", sa.Integer, sa.ForeignKey("runs.id")),
        sa.Column("target", sa.String(200)),
        sa.Column("model_name", sa.String(120)),
        sa.Column("family", sa.String(60)),
        sa.Column("rank", sa.Integer, nullable=True),
        sa.Column("score", sa.Float, nullable=True),
        sa.Column("status", sa.String(30)),
        sa.Column("reason", sa.Text),
        sa.Column("metrics_json", sa.Text),
        sa.Column("formula", sa.Text),
        sa.Column("artifact_path", sa.Text),
    )
    op.create_table(
        "selections",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("run_id", sa.Integer, sa.ForeignKey("runs.id")),
        sa.Column("target", sa.String(200)),
        sa.Column("model_name", sa.String(120)),
        sa.Column("note", sa.Text),
        sa.Column("selected_at", sa.DateTime),
        sa.Column("active", sa.Boolean),
    )
    op.create_table(
        "audit_log",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("run_id", sa.Integer, nullable=True),
        sa.Column("ts", sa.DateTime),
        sa.Column("level", sa.String(10)),
        sa.Column("category", sa.String(60)),
        sa.Column("message", sa.Text),
        sa.Column("detail_json", sa.Text),
    )
    op.create_table(
        "snapshots",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("run_id", sa.Integer, nullable=True),
        sa.Column("target", sa.String(200)),
        sa.Column("name", sa.String(200)),
        sa.Column("created_at", sa.DateTime),
        sa.Column("kpis_json", sa.Text),
        sa.Column("payload", sa.LargeBinary),
    )
    op.create_table(
        "connections",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("name", sa.String(200), unique=True),
        sa.Column("driver", sa.String(30)),
        sa.Column("host", sa.String(300)),
        sa.Column("port", sa.Integer, nullable=True),
        sa.Column("database", sa.String(300)),
        sa.Column("username", sa.String(200)),
        sa.Column("options_json", sa.Text),
        sa.Column("secret_blob", sa.Text),
        sa.Column("created_at", sa.DateTime),
    )


def downgrade() -> None:
    for t in ("connections", "snapshots", "audit_log", "selections", "model_results",
              "auto_resolutions", "runs", "datasets", "health"):
        op.drop_table(t)
