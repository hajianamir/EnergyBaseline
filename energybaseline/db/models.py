"""SQLAlchemy 2.x ORM models (SQLite by default)."""
from __future__ import annotations

import datetime as dt

from sqlalchemy import Boolean, DateTime, Float, ForeignKey, Integer, LargeBinary, String, Text
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


def _now() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc).replace(tzinfo=None)


class Base(DeclarativeBase):
    pass


class Health(Base):
    __tablename__ = "health"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    ts: Mapped[dt.datetime] = mapped_column(DateTime, default=_now)
    note: Mapped[str] = mapped_column(String(200), default="")


class Dataset(Base):
    __tablename__ = "datasets"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(300))
    source_path: Mapped[str] = mapped_column(Text, default="")
    sha256: Mapped[str] = mapped_column(String(64), default="")
    n_rows: Mapped[int] = mapped_column(Integer, default=0)
    n_cols: Mapped[int] = mapped_column(Integer, default=0)
    schema_json: Mapped[str] = mapped_column(Text, default="{}")
    imported_at: Mapped[dt.datetime] = mapped_column(DateTime, default=_now)


class Run(Base):
    __tablename__ = "runs"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    dataset_id: Mapped[int | None] = mapped_column(ForeignKey("datasets.id"), nullable=True)
    started_at: Mapped[dt.datetime] = mapped_column(DateTime, default=_now)
    finished_at: Mapped[dt.datetime | None] = mapped_column(DateTime, nullable=True)
    status: Mapped[str] = mapped_column(String(30), default="running")
    seed: Mapped[int] = mapped_column(Integer, default=0)
    targets_json: Mapped[str] = mapped_column(Text, default="[]")
    config_json: Mapped[str] = mapped_column(Text, default="{}")
    versions_json: Mapped[str] = mapped_column(Text, default="{}")
    summary_json: Mapped[str] = mapped_column(Text, default="{}")
    results_path: Mapped[str] = mapped_column(Text, default="")


class AutoResolution(Base):
    __tablename__ = "auto_resolutions"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    run_id: Mapped[int] = mapped_column(ForeignKey("runs.id"))
    target: Mapped[str] = mapped_column(String(200), default="")
    setting: Mapped[str] = mapped_column(String(100))
    mode: Mapped[str] = mapped_column(String(20))
    value: Mapped[str] = mapped_column(Text, default="")
    signals: Mapped[str] = mapped_column(Text, default="")
    rule: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[dt.datetime] = mapped_column(DateTime, default=_now)


class ModelResult(Base):
    __tablename__ = "model_results"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    run_id: Mapped[int] = mapped_column(ForeignKey("runs.id"))
    target: Mapped[str] = mapped_column(String(200))
    model_name: Mapped[str] = mapped_column(String(120))
    family: Mapped[str] = mapped_column(String(60), default="")
    rank: Mapped[int | None] = mapped_column(Integer, nullable=True)
    score: Mapped[float | None] = mapped_column(Float, nullable=True)
    status: Mapped[str] = mapped_column(String(30), default="candidate")
    reason: Mapped[str] = mapped_column(Text, default="")
    metrics_json: Mapped[str] = mapped_column(Text, default="{}")
    formula: Mapped[str] = mapped_column(Text, default="")
    artifact_path: Mapped[str] = mapped_column(Text, default="")


class Selection(Base):
    __tablename__ = "selections"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    run_id: Mapped[int] = mapped_column(ForeignKey("runs.id"))
    target: Mapped[str] = mapped_column(String(200))
    model_name: Mapped[str] = mapped_column(String(120))
    note: Mapped[str] = mapped_column(Text, default="")
    selected_at: Mapped[dt.datetime] = mapped_column(DateTime, default=_now)
    active: Mapped[bool] = mapped_column(Boolean, default=True)


class AuditLog(Base):
    __tablename__ = "audit_log"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    run_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    ts: Mapped[dt.datetime] = mapped_column(DateTime, default=_now)
    level: Mapped[str] = mapped_column(String(10), default="INFO")
    category: Mapped[str] = mapped_column(String(60), default="")
    message: Mapped[str] = mapped_column(Text, default="")
    detail_json: Mapped[str] = mapped_column(Text, default="")


class Snapshot(Base):
    __tablename__ = "snapshots"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    run_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    target: Mapped[str] = mapped_column(String(200), default="")
    name: Mapped[str] = mapped_column(String(200), default="")
    created_at: Mapped[dt.datetime] = mapped_column(DateTime, default=_now)
    kpis_json: Mapped[str] = mapped_column(Text, default="{}")
    payload: Mapped[bytes] = mapped_column(LargeBinary, default=b"")


class Connection(Base):
    __tablename__ = "connections"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(200), unique=True)
    driver: Mapped[str] = mapped_column(String(30))
    host: Mapped[str] = mapped_column(String(300), default="")
    port: Mapped[int | None] = mapped_column(Integer, nullable=True)
    database: Mapped[str] = mapped_column(String(300), default="")
    username: Mapped[str] = mapped_column(String(200), default="")
    options_json: Mapped[str] = mapped_column(Text, default="{}")
    secret_blob: Mapped[str] = mapped_column(Text, default="")  # encrypted password + query templates
    created_at: Mapped[dt.datetime] = mapped_column(DateTime, default=_now)
