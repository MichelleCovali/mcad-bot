"""Transactional incident writes shared by API and silent historical importers."""
import json
import math
import re
import hashlib
from datetime import date, datetime
from urllib.parse import urlsplit, urlunsplit

from sqlalchemy import select, text

from .models import Actor, Delivery, ImportRow, Incident, IncidentSource, Location, Organisation, OutboxEvent, Subscriber, utcnow


RELATION_FIELDS = {"sources", "reference_urls", "organisations", "actors", "locations"}
WRITE_FIELDS = set(Incident.__table__.columns.keys()) - {"id", "created_at", "updated_at", "notification_suppressed"}
INTEGER_FIELDS = {"year", "year_end"}
NULLABLE_FIELDS = {"external_id", "year", "year_end", "date_text", "date_start", "source_url", "latitude", "longitude"}


def snake_case(key):
    return re.sub(r"(?<!^)(?=[A-Z])", "_", key).lower()


def camel_case(key):
    first, *rest = key.split("_")
    return first + "".join(word.title() for word in rest)


def iso(value):
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.isoformat(timespec="seconds") + "Z"
    return value.isoformat()


def safe_url(value):
    if value is None or value == "":
        return None
    if not isinstance(value, str) or len(value) > 4096 or any(ord(c) < 32 for c in value):
        raise ValueError("source URLs must be ordinary http(s) URLs")
    value = value.strip()
    try:
        parsed = urlsplit(value)
        if parsed.scheme.lower() not in {"http", "https"} or not parsed.hostname or parsed.username or parsed.password:
            raise ValueError
        # Validate a malformed port without making any network request.
        parsed.port
    except ValueError:
        raise ValueError("source URLs must be ordinary http(s) URLs") from None
    return urlunsplit((parsed.scheme.lower(), parsed.netloc.lower(), parsed.path or "/", parsed.query, ""))


def normalize_payload(payload, existing=None):
    if not isinstance(payload, dict):
        raise ValueError("incident must be an object")
    values = {snake_case(key): value for key, value in payload.items()}
    unknown = set(values) - WRITE_FIELDS - RELATION_FIELDS
    if unknown:
        raise ValueError("unknown incident fields: " + ", ".join(sorted(unknown)))
    if "title" in values:
        if not isinstance(values["title"], str) or not values["title"].strip():
            raise ValueError("title is required")
        values["title"] = values["title"].strip()
    elif existing is None:
        raise ValueError("title is required")
    for key, value in list(values.items()):
        if key in RELATION_FIELDS or key in {"raw_fields", "date_start", "latitude", "longitude"} | INTEGER_FIELDS:
            continue
        if value is None:
            values[key] = None if key in NULLABLE_FIELDS else ""
        elif not isinstance(value, str):
            raise ValueError(f"{key} must be a string")
        elif len(value) > (4000 if key == "title" else 100000):
            raise ValueError(f"{key} is too long")
    for key in INTEGER_FIELDS:
        if key in values and values[key] is not None:
            value = values[key]
            if isinstance(value, bool) or not isinstance(value, int) or not 1900 <= value <= date.today().year:
                raise ValueError(f"{key} must be an integer between 1900 and the current year, or null")
    effective_year = values.get("year", getattr(existing, "year", None))
    effective_end = values.get("year_end", getattr(existing, "year_end", None))
    if effective_year and effective_end and effective_end < effective_year:
        raise ValueError("year_end cannot precede year")
    if "latitude" in values or "longitude" in values:
        if "latitude" not in values or "longitude" not in values:
            raise ValueError("latitude and longitude must be supplied together")
        lat, lng = values["latitude"], values["longitude"]
        if (lat is None) != (lng is None):
            raise ValueError("latitude and longitude must both be null or both numbers")
        if lat is not None:
            for key, limit in [("latitude", 90), ("longitude", 180)]:
                value = values[key]
                if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or abs(value) > limit:
                    raise ValueError(f"{key} is outside its valid coordinate range")
    if "status" in values and values["status"] not in {"pending", "published"}:
        raise ValueError("status must be pending or published")
    if "source_url" in values:
        values["source_url"] = safe_url(values["source_url"])
    if "external_id" in values:
        value = values["external_id"]
        values["external_id"] = value.strip() or None if value is not None else None
        if value and len(value) > 255:
            raise ValueError("external_id is too long")
    if "date_start" in values and values["date_start"] is not None:
        value = values["date_start"]
        try:
            values["date_start"] = value if isinstance(value, date) else date.fromisoformat(value)
        except (TypeError, ValueError):
            raise ValueError("date_start must be an ISO date or null") from None
    if "raw_fields" in values:
        if not isinstance(values["raw_fields"], dict):
            raise ValueError("raw_fields must be an object")
        try:
            json.dumps(values["raw_fields"], allow_nan=False)
        except (ValueError, TypeError):
            raise ValueError("raw_fields must contain valid JSON values") from None
    for field in RELATION_FIELDS & set(values):
        if not isinstance(values[field], list):
            raise ValueError(f"{field} must be an array")
    return values


def _replace_relations(incident, values):
    if "reference_urls" in values or "sources" in values or "source_url" in values:
        sources = values.pop("sources", None)
        urls = values.pop("reference_urls", None)
        if sources is not None or urls is not None:
            existing_sources = {source.url: source for source in incident.sources}
            updated_sources = []
            seen = set()
            for raw in (sources or []) + [{"url": url} for url in (urls or [])]:
                if not isinstance(raw, dict):
                    raise ValueError("sources must contain objects")
                item = {snake_case(key): value for key, value in raw.items()}
                url = safe_url(item.get("url"))
                if url and url not in seen:
                    seen.add(url)
                    source = existing_sources.get(url) or IncidentSource(url=url)
                    source.short_name = str(item.get("short_name") or "")
                    source.raw_text = str(item.get("raw_text") or "")
                    updated_sources.append(source)
            incident.sources[:] = updated_sources
        if incident.source_url and all(source.url != incident.source_url for source in incident.sources):
            incident.sources.append(IncidentSource(url=incident.source_url, short_name=incident.reference_short or ""))
    for field, model in [("organisations", Organisation), ("actors", Actor), ("locations", Location)]:
        if field not in values:
            continue
        children = getattr(incident, field)
        children.clear()
        allowed = set(model.__table__.columns.keys()) - {"id", "incident_id"}
        for raw in values.pop(field):
            if not isinstance(raw, dict):
                raise ValueError(f"{field} entries must be objects")
            item = {snake_case(key): value for key, value in raw.items()}
            if set(item) - allowed:
                raise ValueError(f"unknown {field} relationship field")
            if field == "locations":
                normalize_payload({"title": "Location", "latitude": item.get("latitude"), "longitude": item.get("longitude")})
            elif not isinstance(item.get("name"), str) or not item["name"].strip():
                raise ValueError(f"{field} entries require a name")
            children.append(model(**item))


def create_publication_event(session, incident):
    if incident.status != "published" or incident.notification_suppressed:
        return None
    event = session.scalar(select(OutboxEvent).where(OutboxEvent.incident_id == incident.id, OutboxEvent.kind == "published"))
    if event:
        return event
    event = OutboxEvent(incident_id=incident.id, payload={
        "id": incident.id, "title": incident.title, "year": incident.year,
        "incidentCountry": incident.incident_country, "sourceUrl": incident.source_url,
    })
    session.add(event)
    session.flush()
    for subscriber in session.scalars(select(Subscriber).where(Subscriber.active.is_(True), Subscriber.subscribed_at <= event.created_at)):
        session.add(Delivery(event_id=event.id, chat_id=subscriber.chat_id))
    return event


def upsert_incident(session, payload, external_id=None, notify=False, import_batch_id=None, source_row=None):
    """Return (incident, created), without committing. Historical imports use notify=False.

    External IDs identify separate source records even when they share an article URL.
    URL fallback is only used when no external ID was supplied.
    """
    incoming = dict(payload)
    if external_id is not None:
        incoming["external_id"] = external_id
    external = incoming.get("external_id", incoming.get("externalId"))
    external = external.strip() if isinstance(external, str) else external
    source_url = safe_url(incoming.get("source_url", incoming.get("sourceUrl")))
    if session.bind.dialect.name == "postgresql" and (external or source_url):
        # Serialize concurrent idempotent creates before checking whether the row exists.
        identity = f"external:{external}" if external else f"source:{source_url}"
        lock = int.from_bytes(hashlib.sha256(identity.encode()).digest()[:8], "big", signed=True)
        session.execute(text("SELECT pg_advisory_xact_lock(:identity)"), {"identity": lock})
    if external:
        incident = session.scalar(select(Incident).where(Incident.external_id == external).with_for_update())
    elif source_url:
        incident = session.scalar(select(Incident).where(Incident.source_url == source_url).order_by(Incident.created_at).with_for_update())
    else:
        incident = None
    created = incident is None
    values = normalize_payload(incoming, existing=incident)
    if created:
        incident = Incident(title=values["title"], notification_suppressed=not notify)
        session.add(incident)
    was_published = incident.status == "published" if not created else False
    for key, value in values.items():
        if key not in RELATION_FIELDS:
            setattr(incident, key, value)
    _replace_relations(incident, values)
    session.flush()
    if import_batch_id is not None and source_row is not None:
        row = session.scalar(select(ImportRow).where(ImportRow.import_batch_id == import_batch_id, ImportRow.source_row == source_row))
        if row is None:
            session.add(ImportRow(import_batch_id=import_batch_id, source_row=source_row, incident_id=incident.id, raw_fields=incident.raw_fields))
        else:
            row.incident_id, row.raw_fields = incident.id, incident.raw_fields
    if notify and not was_published and incident.status == "published":
        create_publication_event(session, incident)
    session.flush()
    return incident, created


def update_incident(session, incident, payload):
    values = normalize_payload(payload, existing=incident)
    was_published = incident.status == "published"
    for key, value in values.items():
        if key not in RELATION_FIELDS:
            setattr(incident, key, value)
    _replace_relations(incident, values)
    incident.updated_at = utcnow()
    session.flush()
    if not was_published and incident.status == "published":
        create_publication_event(session, incident)
    return incident


def incident_dict(incident, details=False):
    excluded = {"notification_suppressed", "raw_fields"}
    data = {camel_case(column.name): getattr(incident, column.name) for column in Incident.__table__.columns if column.name not in excluded}
    for key in ["dateStart", "createdAt", "updatedAt"]:
        data[key] = iso(data[key])
    if details:
        data["rawFields"] = incident.raw_fields
        data["sources"] = [{"url": x.url, "shortName": x.short_name, "rawText": x.raw_text} for x in incident.sources]
        data["organisations"] = [{"name": x.name, "country": x.country, "role": x.role} for x in incident.organisations]
        data["actors"] = [{"name": x.name, "country": x.country} for x in incident.actors]
        data["locations"] = [{"name": x.name, "country": x.country, "latitude": x.latitude, "longitude": x.longitude, "precision": x.precision, "source": x.source} for x in incident.locations]
        data["provenance"] = [{"importBatchId": x.import_batch_id, "sourceRow": x.source_row} for x in incident.provenance]
    return data


def candidate_dict(candidate):
    return {"id": candidate.id, "title": candidate.title, "sourceUrl": candidate.source_url,
            "summary": candidate.summary, "matchedKeywords": candidate.matched_keywords,
            "status": candidate.status, "baseline": candidate.baseline,
            "firstSeenAt": iso(candidate.first_seen_at), "incidentId": candidate.incident_id}
