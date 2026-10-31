"""SQLAlchemy models (spec §12).

Traceability chain: every number on screen comes from an ``Assessment`` (or a run's
stored tool results), every assessment lists the ``SourceFetch`` ids it was computed
from, and every ``SourceFetch`` stores the exact raw API payload. That chain is how
AC-02 ("recompute the probability by hand from the stored raw response") is checked.
"""
from __future__ import annotations

import datetime as dt
import uuid

from sqlalchemy import (
    JSON,
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    LargeBinary,
    String,
    Text,
    event,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column
from sqlalchemy.types import TypeDecorator


def utcnow() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc)


def new_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:16]}"


class UTCDateTime(TypeDecorator):
    """Always store and return timezone-aware UTC datetimes (SQLite drops tzinfo otherwise)."""

    impl = DateTime(timezone=True)
    cache_ok = True

    def process_bind_param(self, value, dialect):
        if value is None:
            return None
        if value.tzinfo is None:
            value = value.replace(tzinfo=dt.timezone.utc)
        return value.astimezone(dt.timezone.utc)

    def process_result_value(self, value, dialect):
        if value is None:
            return None
        if value.tzinfo is None:
            return value.replace(tzinfo=dt.timezone.utc)
        return value.astimezone(dt.timezone.utc)


class Base(DeclarativeBase):
    type_annotation_map = {dt.datetime: UTCDateTime}


class District(Base):
    __tablename__ = "districts"
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    name: Mapped[str] = mapped_column(String(128))
    state: Mapped[str] = mapped_column(String(64))
    aliases: Mapped[list] = mapped_column(JSON, default=list)
    boundary: Mapped[dict | None] = mapped_column(JSON, nullable=True)  # GeoJSON geometry
    boundary_source: Mapped[str | None] = mapped_column(String(256), nullable=True)
    centroid_lat: Mapped[float] = mapped_column(Float)
    centroid_lon: Mapped[float] = mapped_column(Float)


class MonitorPoint(Base):
    __tablename__ = "monitor_points"
    id: Mapped[str] = mapped_column(String(96), primary_key=True)
    district_id: Mapped[str] = mapped_column(ForeignKey("districts.id"))
    kind: Mapped[str] = mapped_column(String(16))  # centroid | river
    lat: Mapped[float] = mapped_column(Float)
    lon: Mapped[float] = mapped_column(Float)
    river_name: Mapped[str | None] = mapped_column(String(128), nullable=True)
    # GloFAS grid adjustments (§8.1): effective coordinates after nudging, or a flag.
    effective_lat: Mapped[float | None] = mapped_column(Float, nullable=True)
    effective_lon: Mapped[float | None] = mapped_column(Float, nullable=True)
    on_modelled_river: Mapped[bool | None] = mapped_column(Boolean, nullable=True)


class SourceFetch(Base):
    __tablename__ = "source_fetches"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=lambda: new_id("f"))
    source: Mapped[str] = mapped_column(String(64), index=True)
    url: Mapped[str] = mapped_column(Text)
    request_params: Mapped[dict] = mapped_column(JSON, default=dict)
    fetched_at: Mapped[dt.datetime] = mapped_column(default=utcnow, index=True)
    model_run_time: Mapped[dt.datetime | None] = mapped_column(nullable=True)
    model_run_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    status: Mapped[str] = mapped_column(String(24))  # ok | error | simulated_outage | invalid
    http_code: Mapped[int | None] = mapped_column(Integer, nullable=True)
    latency_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    attempts: Mapped[int] = mapped_column(Integer, default=1)
    raw_payload: Mapped[dict | list | str | None] = mapped_column(JSON, nullable=True)
    payload_sha256: Mapped[str | None] = mapped_column(String(64), nullable=True)
    payload_bytes: Mapped[int | None] = mapped_column(Integer, nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    simulated: Mapped[bool] = mapped_column(Boolean, default=False)


class Assessment(Base):
    __tablename__ = "assessments"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=lambda: new_id("a"))
    district_id: Mapped[str] = mapped_column(ForeignKey("districts.id"), index=True)
    hazard: Mapped[str] = mapped_column(String(16))  # rain | river | heat
    computed_at: Mapped[dt.datetime] = mapped_column(default=utcnow, index=True)
    data_time: Mapped[dt.datetime | None] = mapped_column(nullable=True)  # fetch time of the data used
    model: Mapped[str | None] = mapped_column(String(64), nullable=True)
    model_run_time: Mapped[dt.datetime | None] = mapped_column(nullable=True)
    model_run_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    n_members: Mapped[int | None] = mapped_column(Integer, nullable=True)  # model's member count
    n_valid: Mapped[int | None] = mapped_column(Integer, nullable=True)
    probabilities: Mapped[dict] = mapped_column(JSON, default=dict)
    base_level: Mapped[str | None] = mapped_column(String(16), nullable=True)
    level: Mapped[str | None] = mapped_column(String(16), nullable=True)  # resolved; None = Quiet
    hysteresis_level: Mapped[str | None] = mapped_column(String(16), nullable=True)
    t_star: Mapped[dt.datetime | None] = mapped_column(nullable=True)
    t_minus_h: Mapped[float | None] = mapped_column(Float, nullable=True)
    onset_time: Mapped[dt.datetime | None] = mapped_column(nullable=True)
    onset_h: Mapped[float | None] = mapped_column(Float, nullable=True)
    degraded: Mapped[bool] = mapped_column(Boolean, default=False)
    stale: Mapped[bool] = mapped_column(Boolean, default=False)
    insufficient: Mapped[bool] = mapped_column(Boolean, default=False)
    flags: Mapped[dict] = mapped_column(JSON, default=dict)
    details: Mapped[dict] = mapped_column(JSON, default=dict)
    fetch_ids: Mapped[list] = mapped_column(JSON, default=list)


Index("ix_assess_district_hazard_time", Assessment.district_id, Assessment.hazard, Assessment.computed_at)


class RiverBaseline(Base):
    __tablename__ = "river_baselines"
    point_id: Mapped[str] = mapped_column(ForeignKey("monitor_points.id"), primary_key=True)
    p95: Mapped[float] = mapped_column(Float)
    p99: Mapped[float] = mapped_column(Float)
    n_days: Mapped[int] = mapped_column(Integer)
    period_start: Mapped[str] = mapped_column(String(10))
    period_end: Mapped[str] = mapped_column(String(10))
    computed_at: Mapped[dt.datetime] = mapped_column(default=utcnow)
    fetch_id: Mapped[str] = mapped_column(String(32))
    lat: Mapped[float] = mapped_column(Float)
    lon: Mapped[float] = mapped_column(Float)


class Trigger(Base):
    __tablename__ = "triggers"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=lambda: new_id("t"))
    district_id: Mapped[str] = mapped_column(ForeignKey("districts.id"), index=True)
    hazard: Mapped[str] = mapped_column(String(16))
    level: Mapped[str] = mapped_column(String(16))
    # active | stand_down_recommended | closed
    status: Mapped[str] = mapped_column(String(32), default="active", index=True)
    window_start: Mapped[dt.datetime | None] = mapped_column(nullable=True)
    window_end: Mapped[dt.datetime | None] = mapped_column(nullable=True)
    t_star: Mapped[dt.datetime | None] = mapped_column(nullable=True)
    opened_at: Mapped[dt.datetime] = mapped_column(default=utcnow)
    updated_at: Mapped[dt.datetime] = mapped_column(default=utcnow)
    closed_at: Mapped[dt.datetime | None] = mapped_column(nullable=True)
    assessment_id: Mapped[str | None] = mapped_column(String(32), nullable=True)
    cap_msg_type: Mapped[str] = mapped_column(String(16), default="Alert")  # Alert | Update | Cancel
    history: Mapped[list] = mapped_column(JSON, default=list)
    state: Mapped[dict] = mapped_column(JSON, default=dict)  # hysteresis bookkeeping


class PlanDocument(Base):
    __tablename__ = "plan_documents"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=lambda: new_id("doc"))
    district_id: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    kind: Mapped[str] = mapped_column(String(16))  # district | city | generic
    title: Mapped[str] = mapped_column(String(512))
    edition_year: Mapped[int | None] = mapped_column(Integer, nullable=True)
    source_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    sha256: Mapped[str] = mapped_column(String(64), index=True)
    pages: Mapped[int] = mapped_column(Integer)
    ocr_pages: Mapped[list] = mapped_column(JSON, default=list)
    low_confidence_pages: Mapped[list] = mapped_column(JSON, default=list)
    ingested_at: Mapped[dt.datetime] = mapped_column(default=utcnow)
    ingested_by: Mapped[str | None] = mapped_column(String(128), nullable=True)
    embedding_backend: Mapped[str | None] = mapped_column(String(64), nullable=True)


class PlanChunk(Base):
    __tablename__ = "plan_chunks"
    id: Mapped[str] = mapped_column(String(80), primary_key=True)
    document_id: Mapped[str] = mapped_column(ForeignKey("plan_documents.id", ondelete="CASCADE"), index=True)
    page: Mapped[int] = mapped_column(Integer)
    section: Mapped[str | None] = mapped_column(String(512), nullable=True)
    text: Mapped[str] = mapped_column(Text)
    ocr: Mapped[bool] = mapped_column(Boolean, default=False)
    embedding: Mapped[bytes | None] = mapped_column(LargeBinary, nullable=True)


class AgentRun(Base):
    __tablename__ = "agent_runs"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=lambda: new_id("run"))
    trigger_id: Mapped[str | None] = mapped_column(String(32), nullable=True, index=True)
    district_id: Mapped[str] = mapped_column(String(64), index=True)
    hazard: Mapped[str] = mapped_column(String(16))
    started_at: Mapped[dt.datetime] = mapped_column(default=utcnow)
    finished_at: Mapped[dt.datetime | None] = mapped_column(nullable=True)
    # running | completed | partial | failed | unavailable
    status: Mapped[str] = mapped_column(String(24), default="running")
    started_by: Mapped[str | None] = mapped_column(String(128), nullable=True)
    model: Mapped[str | None] = mapped_column(String(64), nullable=True)
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    tool_calls: Mapped[int] = mapped_column(Integer, default=0)
    tokens_in: Mapped[int] = mapped_column(Integer, default=0)
    tokens_out: Mapped[int] = mapped_column(Integer, default=0)
    plan: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    verification: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    state: Mapped[dict] = mapped_column(JSON, default=dict)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)


class AgentEvent(Base):
    __tablename__ = "agent_events"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    run_id: Mapped[str] = mapped_column(ForeignKey("agent_runs.id", ondelete="CASCADE"), index=True)
    seq: Mapped[int] = mapped_column(Integer)
    ts: Mapped[dt.datetime] = mapped_column(default=utcnow)
    type: Mapped[str] = mapped_column(String(24))  # step | tool_call | tool_result | retry | error | ...
    payload: Mapped[dict] = mapped_column(JSON, default=dict)


class ActionItemRow(Base):
    __tablename__ = "action_items"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=lambda: new_id("act"))
    run_id: Mapped[str] = mapped_column(ForeignKey("agent_runs.id", ondelete="CASCADE"), index=True)
    key: Mapped[str] = mapped_column(String(16))  # A1, A2, ...
    phase: Mapped[str] = mapped_column(String(16))
    action: Mapped[str] = mapped_column(Text)
    responsible: Mapped[str | None] = mapped_column(String(256), nullable=True)
    location_refs: Mapped[list] = mapped_column(JSON, default=list)
    source: Mapped[str] = mapped_column(String(24))
    citations: Mapped[list] = mapped_column(JSON, default=list)
    rationale: Mapped[str] = mapped_column(Text, default="")
    status: Mapped[str] = mapped_column(String(16), default="proposed")
    edited_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    problems: Mapped[list] = mapped_column(JSON, default=list)  # unresolved VERIFY problems
    position: Mapped[int] = mapped_column(Integer, default=0)


class Communication(Base):
    __tablename__ = "communications"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=lambda: new_id("com"))
    run_id: Mapped[str] = mapped_column(ForeignKey("agent_runs.id", ondelete="CASCADE"), index=True)
    kind: Mapped[str] = mapped_column(String(24))  # briefing | public_en | public_ta | cap
    content: Mapped[str] = mapped_column(Text)
    valid: Mapped[bool] = mapped_column(Boolean, default=False)
    validation: Mapped[dict] = mapped_column(JSON, default=dict)
    status: Mapped[str] = mapped_column(String(16), default="proposed")  # proposed | approved | rejected | published
    updated_at: Mapped[dt.datetime] = mapped_column(default=utcnow)


class Approval(Base):
    """Append-only audit log (§9.2). Updates and deletes are blocked below."""

    __tablename__ = "approvals"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    target_type: Mapped[str] = mapped_column(String(32))
    target_id: Mapped[str] = mapped_column(String(64), index=True)
    decision: Mapped[str] = mapped_column(String(32))
    before: Mapped[str | None] = mapped_column(Text, nullable=True)
    after: Mapped[str | None] = mapped_column(Text, nullable=True)
    user: Mapped[str] = mapped_column(String(128))
    role: Mapped[str] = mapped_column(String(16))
    ts: Mapped[dt.datetime] = mapped_column(default=utcnow)
    note: Mapped[str | None] = mapped_column(Text, nullable=True)


@event.listens_for(Approval, "before_update")
def _block_update(*_):  # pragma: no cover - defensive
    raise RuntimeError("approvals is append-only")


@event.listens_for(Approval, "before_delete")
def _block_delete(*_):  # pragma: no cover - defensive
    raise RuntimeError("approvals is append-only")


class PublicAlert(Base):
    __tablename__ = "public_alerts"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=lambda: new_id("pa"))
    run_id: Mapped[str] = mapped_column(String(32), index=True)
    district_id: Mapped[str] = mapped_column(String(64))
    hazard: Mapped[str] = mapped_column(String(16))
    level: Mapped[str] = mapped_column(String(16))
    msg_type: Mapped[str] = mapped_column(String(16))
    message_en: Mapped[str] = mapped_column(Text)
    message_ta: Mapped[str] = mapped_column(Text)
    cap_communication_id: Mapped[str | None] = mapped_column(String(32), nullable=True)
    published_at: Mapped[dt.datetime] = mapped_column(default=utcnow)
    published_by: Mapped[str] = mapped_column(String(128))
    data_timestamps: Mapped[dict] = mapped_column(JSON, default=dict)


class Geocode(Base):
    __tablename__ = "geocodes"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(256), index=True)
    district_id: Mapped[str] = mapped_column(String(64), index=True)
    status: Mapped[str] = mapped_column(String(16))  # found | not_found
    lat: Mapped[float | None] = mapped_column(Float, nullable=True)
    lon: Mapped[float | None] = mapped_column(Float, nullable=True)
    confidence: Mapped[float | None] = mapped_column(Float, nullable=True)
    osm_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    display_name: Mapped[str | None] = mapped_column(Text, nullable=True)
    fetched_at: Mapped[dt.datetime] = mapped_column(default=utcnow)
    fetch_id: Mapped[str | None] = mapped_column(String(32), nullable=True)


class FacilityCache(Base):
    __tablename__ = "facility_cache"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    district_id: Mapped[str] = mapped_column(String(64), index=True)
    types_key: Mapped[str] = mapped_column(String(256))
    fetched_at: Mapped[dt.datetime] = mapped_column(default=utcnow)
    fetch_id: Mapped[str] = mapped_column(String(32))
    features: Mapped[list] = mapped_column(JSON, default=list)


class ReplayRun(Base):
    __tablename__ = "replay_runs"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=lambda: new_id("rp"))
    event_key: Mapped[str] = mapped_column(String(64))
    started_at: Mapped[dt.datetime] = mapped_column(default=utcnow)
    finished_at: Mapped[dt.datetime | None] = mapped_column(nullable=True)
    status: Mapped[str] = mapped_column(String(16), default="running")
    result: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    fetch_ids: Mapped[list] = mapped_column(JSON, default=list)


class ScanRun(Base):
    __tablename__ = "scan_runs"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=lambda: new_id("scan"))
    started_at: Mapped[dt.datetime] = mapped_column(default=utcnow, index=True)
    finished_at: Mapped[dt.datetime | None] = mapped_column(nullable=True)
    trigger: Mapped[str] = mapped_column(String(16))  # schedule | manual | startup | catch_up
    status: Mapped[str] = mapped_column(String(16), default="running")
    summary: Mapped[dict] = mapped_column(JSON, default=dict)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)


# ---------------------------------------------------------------------------------------------
# Features 1-6. New tables only, so an existing database upgrades through create_all with no
# migration step.


class PlanRecord(Base):
    """Feature 1: one structured fact extracted from a plan page, with its verbatim quote."""

    __tablename__ = "plan_records"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    document_id: Mapped[str] = mapped_column(ForeignKey("plan_documents.id", ondelete="CASCADE"), index=True)
    district_id: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    kind: Mapped[str] = mapped_column(String(16), index=True)  # contact|shelter|resource|responsibility|procedure|asset
    label: Mapped[str] = mapped_column(Text)
    fields: Mapped[dict] = mapped_column(JSON, default=dict)
    page: Mapped[int] = mapped_column(Integer)
    quote: Mapped[str] = mapped_column(Text)


class PlanStructure(Base):
    """Which documents have been structured, by which extractor version."""

    __tablename__ = "plan_structure"
    document_id: Mapped[str] = mapped_column(String(32), primary_key=True)
    version: Mapped[int] = mapped_column(Integer)
    extracted_at: Mapped[dt.datetime] = mapped_column(default=utcnow)
    counts: Mapped[dict] = mapped_column(JSON, default=dict)
    malformed_numbers: Mapped[list] = mapped_column(JSON, default=list)


class ActionTask(Base):
    """Feature 6: execution tracking for an approved action."""

    __tablename__ = "action_tasks"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=lambda: new_id("task"))
    action_id: Mapped[str] = mapped_column(String(32), unique=True, index=True)
    run_id: Mapped[str] = mapped_column(String(32), index=True)
    district_id: Mapped[str] = mapped_column(String(64), index=True)
    department: Mapped[str | None] = mapped_column(String(256), nullable=True)
    # pending | in_progress | completed | blocked | cancelled
    status: Mapped[str] = mapped_column(String(16), default="pending", index=True)
    due_at: Mapped[dt.datetime | None] = mapped_column(nullable=True)
    blocker: Mapped[str | None] = mapped_column(Text, nullable=True)
    note: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[dt.datetime] = mapped_column(default=utcnow)
    updated_at: Mapped[dt.datetime] = mapped_column(default=utcnow)
    updated_by: Mapped[str | None] = mapped_column(String(128), nullable=True)
    completed_at: Mapped[dt.datetime | None] = mapped_column(nullable=True)


class ReadinessCheck(Base):
    """Feature 3: can an action be carried out? One row per (action, requirement)."""

    __tablename__ = "readiness_checks"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    action_id: Mapped[str] = mapped_column(String(32), index=True)
    requirement: Mapped[str] = mapped_column(String(32))
    status: Mapped[str] = mapped_column(String(16))  # ready | unavailable | unverified
    reason: Mapped[str] = mapped_column(Text, default="")
    evidence: Mapped[list] = mapped_column(JSON, default=list)
    checked_at: Mapped[dt.datetime] = mapped_column(default=utcnow)
    verified_by: Mapped[str | None] = mapped_column(String(128), nullable=True)  # officer confirmation
    verified_at: Mapped[dt.datetime | None] = mapped_column(nullable=True)


class ImpactAssessment(Base):
    """Feature 2: where in the district the hazard is likely to hit hardest."""

    __tablename__ = "impact_assessments"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=lambda: new_id("imp"))
    district_id: Mapped[str] = mapped_column(String(64), index=True)
    computed_at: Mapped[dt.datetime] = mapped_column(default=utcnow, index=True)
    model_run_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    data: Mapped[dict] = mapped_column(JSON, default=dict)
    fetch_ids: Mapped[list] = mapped_column(JSON, default=list)


class TerrainPoint(Base):
    """Ground elevation at a point (Copernicus DEM via Open-Meteo). Terrain does not change: cached forever."""

    __tablename__ = "terrain_points"
    key: Mapped[str] = mapped_column(String(32), primary_key=True)  # "lat,lon" at 4 decimals
    lat: Mapped[float] = mapped_column(Float)
    lon: Mapped[float] = mapped_column(Float)
    elevation_m: Mapped[float | None] = mapped_column(Float, nullable=True)
    fetch_id: Mapped[str | None] = mapped_column(String(32), nullable=True)
    fetched_at: Mapped[dt.datetime] = mapped_column(default=utcnow)


class PlaceName(Base):
    """Reverse geocode of a point (Nominatim), cached forever (usage policy)."""

    __tablename__ = "place_names"
    key: Mapped[str] = mapped_column(String(32), primary_key=True)
    name: Mapped[str | None] = mapped_column(String(256), nullable=True)
    display_name: Mapped[str | None] = mapped_column(Text, nullable=True)
    place_type: Mapped[str | None] = mapped_column(String(64), nullable=True)
    place_rank: Mapped[int | None] = mapped_column(Integer, nullable=True)
    population: Mapped[str | None] = mapped_column(String(32), nullable=True)
    osm_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    fetch_id: Mapped[str | None] = mapped_column(String(32), nullable=True)
    fetched_at: Mapped[dt.datetime] = mapped_column(default=utcnow)
