from datetime import datetime, timezone
from uuid import uuid4

from sqlalchemy import Boolean, CheckConstraint, Date, DateTime, Float, ForeignKey, Integer, JSON, String, Text, UniqueConstraint
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


def utcnow():
    """Naive UTC timestamps are portable between PostgreSQL and SQLite."""
    return datetime.now(timezone.utc).replace(tzinfo=None)


def uuid():
    return str(uuid4())


class Base(DeclarativeBase):
    pass


class SchemaVersion(Base):
    __tablename__ = "schema_versions"
    version: Mapped[int] = mapped_column(primary_key=True)
    applied_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class ImportBatch(Base):
    __tablename__ = "import_batches"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid)
    source_name: Mapped[str] = mapped_column(Text)
    source_sha256: Mapped[str] = mapped_column(String(64), index=True)
    row_count: Mapped[int] = mapped_column(Integer, default=0)
    metadata_json: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class Incident(Base):
    __tablename__ = "incidents"
    __table_args__ = (
        CheckConstraint("(latitude IS NULL AND longitude IS NULL) OR (latitude IS NOT NULL AND longitude IS NOT NULL)", name="paired_coordinates"),
        CheckConstraint("latitude IS NULL OR (latitude >= -90 AND latitude <= 90)", name="latitude_range"),
        CheckConstraint("longitude IS NULL OR (longitude >= -180 AND longitude <= 180)", name="longitude_range"),
        CheckConstraint("status IN ('published', 'pending')", name="incident_status"),
    )
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid)
    external_id: Mapped[str | None] = mapped_column(String(255), unique=True, nullable=True)
    title: Mapped[str] = mapped_column(Text)
    description: Mapped[str] = mapped_column(Text, default="")
    year: Mapped[int | None] = mapped_column(Integer, nullable=True, index=True)
    date_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    date_start: Mapped[object | None] = mapped_column(Date, nullable=True)
    date_precision: Mapped[str] = mapped_column(String(32), default="unknown")
    victim_country: Mapped[str] = mapped_column(Text, default="", index=True)
    victim_type: Mapped[str] = mapped_column(Text, default="")
    impact_area: Mapped[str] = mapped_column(Text, default="")
    incident_country: Mapped[str] = mapped_column(Text, default="", index=True)
    attacker_country: Mapped[str] = mapped_column(Text, default="")
    location_name: Mapped[str] = mapped_column(Text, default="")
    latitude: Mapped[float | None] = mapped_column(Float, nullable=True)
    longitude: Mapped[float | None] = mapped_column(Float, nullable=True)
    location_precision: Mapped[str] = mapped_column(String(64), default="unknown")
    location_source: Mapped[str] = mapped_column(Text, default="")
    status: Mapped[str] = mapped_column(String(32), default="published", index=True)
    source_url: Mapped[str | None] = mapped_column(Text, nullable=True, index=True)
    raw_fields: Mapped[dict] = mapped_column(JSON, default=dict)
    notification_suppressed: Mapped[bool] = mapped_column(Boolean, default=False)
    qa: Mapped[str] = mapped_column(Text, default="")
    reference_number: Mapped[str] = mapped_column(Text, default="", index=True)
    month: Mapped[str] = mapped_column(Text, default="")
    year_end: Mapped[int | None] = mapped_column(Integer, nullable=True)
    victim_identity: Mapped[str] = mapped_column(Text, default="")
    victim_type_short: Mapped[str] = mapped_column(Text, default="")
    affected_system: Mapped[str] = mapped_column(Text, default="")
    method: Mapped[str] = mapped_column(Text, default="")
    social_engineering_technique: Mapped[str] = mapped_column(Text, default="")
    malware_type: Mapped[str] = mapped_column(Text, default="")
    ransomware_type: Mapped[str] = mapped_column(Text, default="")
    attack_pattern: Mapped[str] = mapped_column(Text, default="")
    motive: Mapped[str] = mapped_column(Text, default="")
    motive_short: Mapped[str] = mapped_column(Text, default="")
    campaign: Mapped[str] = mapped_column(Text, default="")
    impact: Mapped[str] = mapped_column(Text, default="")
    impact_short: Mapped[str] = mapped_column(Text, default="")
    countermeasures: Mapped[str] = mapped_column(Text, default="")
    threat_actor: Mapped[str] = mapped_column(Text, default="")
    additional_info: Mapped[str] = mapped_column(Text, default="")
    summary: Mapped[str] = mapped_column(Text, default="")
    reference_short: Mapped[str] = mapped_column(Text, default="")
    google_map_location: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, index=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow, index=True)
    sources: Mapped[list["IncidentSource"]] = relationship(cascade="all, delete-orphan", back_populates="incident")
    organisations: Mapped[list["Organisation"]] = relationship(cascade="all, delete-orphan", back_populates="incident")
    actors: Mapped[list["Actor"]] = relationship(cascade="all, delete-orphan", back_populates="incident")
    locations: Mapped[list["Location"]] = relationship(cascade="all, delete-orphan", back_populates="incident")
    provenance: Mapped[list["ImportRow"]] = relationship(back_populates="incident")


class IncidentSource(Base):
    __tablename__ = "incident_sources"
    __table_args__ = (UniqueConstraint("incident_id", "url"),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid)
    incident_id: Mapped[str] = mapped_column(ForeignKey("incidents.id", ondelete="CASCADE"), index=True)
    url: Mapped[str] = mapped_column(Text)
    short_name: Mapped[str] = mapped_column(Text, default="")
    raw_text: Mapped[str] = mapped_column(Text, default="")
    incident: Mapped[Incident] = relationship(back_populates="sources")


class Organisation(Base):
    __tablename__ = "organisations"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid)
    incident_id: Mapped[str] = mapped_column(ForeignKey("incidents.id", ondelete="CASCADE"), index=True)
    name: Mapped[str] = mapped_column(Text)
    country: Mapped[str] = mapped_column(Text, default="")
    role: Mapped[str] = mapped_column(String(64), default="victim")
    incident: Mapped[Incident] = relationship(back_populates="organisations")


class Actor(Base):
    __tablename__ = "actors"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid)
    incident_id: Mapped[str] = mapped_column(ForeignKey("incidents.id", ondelete="CASCADE"), index=True)
    name: Mapped[str] = mapped_column(Text)
    country: Mapped[str] = mapped_column(Text, default="")
    incident: Mapped[Incident] = relationship(back_populates="actors")


class Location(Base):
    __tablename__ = "locations"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid)
    incident_id: Mapped[str] = mapped_column(ForeignKey("incidents.id", ondelete="CASCADE"), index=True)
    name: Mapped[str] = mapped_column(Text, default="")
    country: Mapped[str] = mapped_column(Text, default="")
    latitude: Mapped[float | None] = mapped_column(Float, nullable=True)
    longitude: Mapped[float | None] = mapped_column(Float, nullable=True)
    precision: Mapped[str] = mapped_column(String(64), default="unknown")
    source: Mapped[str] = mapped_column(Text, default="")
    incident: Mapped[Incident] = relationship(back_populates="locations")


class ImportRow(Base):
    __tablename__ = "import_rows"
    __table_args__ = (UniqueConstraint("import_batch_id", "source_row"),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid)
    import_batch_id: Mapped[str] = mapped_column(ForeignKey("import_batches.id"), index=True)
    source_row: Mapped[int] = mapped_column(Integer)
    incident_id: Mapped[str | None] = mapped_column(ForeignKey("incidents.id"), nullable=True, index=True)
    raw_fields: Mapped[dict] = mapped_column(JSON, default=dict)
    incident: Mapped[Incident | None] = relationship(back_populates="provenance")


class Candidate(Base):
    __tablename__ = "candidates"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid)
    source_url: Mapped[str] = mapped_column(Text, unique=True)
    title: Mapped[str] = mapped_column(Text)
    summary: Mapped[str] = mapped_column(Text, default="")
    matched_keywords: Mapped[list] = mapped_column(JSON, default=list)
    status: Mapped[str] = mapped_column(String(32), default="pending", index=True)
    baseline: Mapped[bool] = mapped_column(Boolean, default=False)
    first_seen_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    incident_id: Mapped[str | None] = mapped_column(ForeignKey("incidents.id"), nullable=True)


class Subscriber(Base):
    __tablename__ = "subscribers"
    chat_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    active: Mapped[bool] = mapped_column(Boolean, default=True, index=True)
    source: Mapped[str] = mapped_column(String(32), default="command")
    subscribed_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)


class OutboxEvent(Base):
    __tablename__ = "outbox_events"
    __table_args__ = (UniqueConstraint("incident_id", "kind"),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid)
    incident_id: Mapped[str] = mapped_column(ForeignKey("incidents.id"), index=True)
    kind: Mapped[str] = mapped_column(String(32), default="published")
    payload: Mapped[dict] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class Delivery(Base):
    __tablename__ = "deliveries"
    __table_args__ = (UniqueConstraint("event_id", "chat_id"),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid)
    event_id: Mapped[str] = mapped_column(ForeignKey("outbox_events.id"), index=True)
    chat_id: Mapped[str] = mapped_column(ForeignKey("subscribers.chat_id"), index=True)
    status: Mapped[str] = mapped_column(String(32), default="pending", index=True)
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    next_attempt_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, index=True)
    lease_owner: Mapped[str | None] = mapped_column(String(36), nullable=True)
    lease_until: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    last_error: Mapped[str | None] = mapped_column(String(64), nullable=True)
    telegram_message_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    sent_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


class WorkerLease(Base):
    __tablename__ = "worker_leases"
    name: Mapped[str] = mapped_column(String(64), primary_key=True)
    owner: Mapped[str] = mapped_column(String(36))
    expires_at: Mapped[datetime] = mapped_column(DateTime)


class WorkerState(Base):
    __tablename__ = "worker_state"
    name: Mapped[str] = mapped_column(String(64), primary_key=True)
    value: Mapped[dict] = mapped_column(JSON, default=dict)


class ProcessedUpdate(Base):
    __tablename__ = "processed_updates"
    update_id: Mapped[int] = mapped_column(Integer, primary_key=True)
    processed_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
