"""Transactional incident writes shared by API and silent historical importers."""
import json
import math
import re
import hashlib
from datetime import date, datetime
from urllib.parse import urlsplit, urlunsplit
from .models import Candidate, IncidentRevision

from sqlalchemy import select, text, update

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


def source_identity(url):
    """Compare validated source URLs without rewriting the stored references."""
    parsed = urlsplit(url)
    default_port = {"http": 80, "https": 443}[parsed.scheme]
    if parsed.port == default_port:
        return urlunsplit(parsed._replace(netloc=parsed.netloc.rsplit(":", 1)[0]))
    return url


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
        existing_sources = {source.url: source for source in incident.sources}
        if sources is not None or urls is not None:
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
            # A replacement list can omit the main reference while source_url
            # still retains it. Reuse that row rather than delete/reinsert the
            # same unique (incident, URL) key, keeping its original evidence.
            incident.sources.append(existing_sources.get(incident.source_url) or
                IncidentSource(url=incident.source_url, short_name=incident.reference_short or ""))
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


def create_publication_event(session, incident, reviewed_candidate_id=None):
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
    already_notified = set()
    if reviewed_candidate_id:
        from .models import CandidateDelivery, CandidateNotification
        # Approval holds the same candidate lock as a candidate delivery. A
        # completed research alert must not become a second alert on approval.
        already_notified = set(session.scalars(
            select(CandidateDelivery.chat_id).join(CandidateNotification,
                CandidateDelivery.event_id == CandidateNotification.id).where(
                    CandidateNotification.candidate_id == reviewed_candidate_id,
                    CandidateDelivery.status == "sent")))
    for subscriber in session.scalars(select(Subscriber).where(Subscriber.active.is_(True), Subscriber.subscribed_at <= event.created_at)):
        if subscriber.chat_id not in already_notified:
            session.add(Delivery(event_id=event.id, chat_id=subscriber.chat_id))
    return event

REVIEW_UPDATE_FIELDS = (
    "title", "summary", "affected_system", "impact", "method",
    "countermeasures", "additional_info",
)


class ReviewConflict(ValueError):
    def __init__(self, code, message):
        super().__init__(message)
        self.code = code


def apply_reviewed_incident_update(
    session, candidate_id, incident_id, payload, reason,
    expected_updated_at, *, independent_sources_confirmed=False,
):
    """Apply an explicit reviewed patch within the caller's transaction."""
    if (
        not isinstance(reason, str)
        or not reason.strip()
        or len(reason) > 2000
    ):
        raise ValueError("Provide an update reason of 1–2000 characters.")

    candidate = session.scalar(
        select(Candidate)
        .where(Candidate.id == candidate_id)
        .with_for_update()
    )
    if candidate is None:
        raise ValueError("Report not found.")
    if candidate.status != "pending":
        raise ReviewConflict("report_resolved", "This report has already been reviewed.")

    if candidate.relationship_type not in {
        "unclassified", "possible_update", "possible_duplicate"
    }:
        raise ValueError("This candidate is not awaiting relationship review.")

    incident = session.scalar(
        select(Incident)
        .where(Incident.id == incident_id)
        .with_for_update()
    )
    if incident is None or incident.status != "published":
        raise ValueError("Choose an existing published incident.")

    if expected_updated_at != incident.updated_at.isoformat():
        raise ReviewConflict(
            "stale_target", "The incident changed since it was opened. Reload and compare."
        )

    fields = {snake_case(key) for key in payload}
    if fields - set(REVIEW_UPDATE_FIELDS) - {"sources"}:
        raise ValueError("This update includes protected fields.")
    for key, value in payload.items():
        if snake_case(key) != "sources" and (not isinstance(value, str) or not value.strip()):
            raise ValueError("Selected changes must contain nonblank text.")

    values = normalize_payload(payload, existing=incident)
    selected_sources = values.pop("sources", [])
    if not selected_sources:
        raise ValueError("Select at least one source supporting this update.")
    existing_sources = merge_incident_sources(incident, [])
    merged_sources = merge_incident_sources(incident, selected_sources)
    existing_urls = {source["url"] for source in existing_sources}
    additions = [source for source in merged_sources if source["url"] not in existing_urls]
    values = {key: value for key, value in values.items() if getattr(incident, key) != value}
    if values:
        from .editorial import _require_sources

        _require_sources([safe_url(source["url"]) for source in selected_sources],
                         independent_sources_confirmed, candidate)
    if "summary" in values:
        values["description"] = values["summary"]
    if not values and not additions:
        raise ValueError("No changed fields or additional sources were supplied.")

    # Copy JSON data so later edits cannot alter the saved history.
    before = json.loads(json.dumps(incident_dict(incident, details=True)))
    evidence = json.loads(json.dumps(candidate.evidence or {}))

    evidence["reviewedUpdateSources"] = list(dict.fromkeys(
        safe_url(source["url"]) for source in selected_sources
    ))

    claimed = session.execute(update(Candidate).where(
        Candidate.id == candidate.id,
        Candidate.status == "pending",
        Candidate.relationship_type == candidate.relationship_type,
    ).values(
        status="approved",
        incident_id=incident.id,
        related_incident_id=incident.id,
        relationship_type="update",
        relationship_reason=reason.strip(),
    ))
    if claimed.rowcount != 1:
        raise ReviewConflict("report_changed", "The report changed during review. Reload and compare.")

    changed = session.execute(update(Incident).where(
        Incident.id == incident.id,
        Incident.status == "published",
        Incident.updated_at == incident.updated_at,
    ).values(**values, updated_at=utcnow()))
    if changed.rowcount != 1:
        raise ReviewConflict(
            "stale_target", "The incident changed since it was opened. Reload and compare."
        )

    incident.sources.extend(IncidentSource(**source) for source in additions)
    session.flush()

    after = json.loads(json.dumps(incident_dict(incident, details=True)))

    session.add(IncidentRevision(
        incident_id=incident.id,
        candidate_id=candidate.id,
        reason=reason.strip(),
        before_snapshot=before,
        after_snapshot=after,
        evidence=evidence,
    ))

    session.flush()
    return incident

class IncidentAlreadyExists(ValueError):
    """A purported new incident shares the identity of an existing record."""


def upsert_incident(session, payload, external_id=None, notify=False, import_batch_id=None, source_row=None,
                    reviewed_candidate_id=None, *, require_new=False):
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
    if require_new and not created:
        raise IncidentAlreadyExists("A record already uses this source or identifier. Select it as the incident to update.")
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
        create_publication_event(session, incident, reviewed_candidate_id=reviewed_candidate_id)
    session.flush()
    return incident, created

def merge_incident_sources(incident, new_sources):
    """Combine sources without removing earlier evidence."""
    if not isinstance(new_sources, list):
        raise ValueError("New sources must be a list.")

    merged = {
        source.url: {
            "url": source.url,
            "short_name": source.short_name,
            "raw_text": source.raw_text,
        }
        for source in incident.sources
    }

    # Preserve the main reference even if it is not in the source list.
    if incident.source_url and incident.source_url not in merged:
        merged[incident.source_url] = {
            "url": incident.source_url,
            "short_name": incident.reference_short or "",
            "raw_text": "",
        }

    identities = set()
    for url in merged:
        try:
            normalized = safe_url(url)
            if normalized:
                identities.add(source_identity(normalized))
        except ValueError:
            # Preserve legacy references that predate URL validation.
            identities.add(url)

    for source in new_sources:
        if not isinstance(source, dict):
            raise ValueError("Each new source must be an object.")

        url = safe_url(source.get("url"))
        if not url:
            raise ValueError("Each new source requires a URL.")

        short_name = source.get(
            "short_name", source.get("shortName", "")
        )
        raw_text = source.get(
            "raw_text", source.get("rawText", "")
        )
        if not isinstance(short_name, str) or not isinstance(raw_text, str):
            raise ValueError("Source names and excerpts must be text.")

        # Keep existing metadata when this URL is already recorded.
        identity = source_identity(url)
        if identity not in identities:
            identities.add(identity)
            merged[url] = {
                "url": url,
                "short_name": short_name,
                "raw_text": raw_text,
            }

    return list(merged.values())

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
    raw = incident.raw_fields or {}
    relations = raw.get("_country_relations")
    country_facets = {}
    if isinstance(relations, dict):
        for field in ("incident_country", "victim_country", "attacker_country"):
            entries = relations.get(field)
            if not isinstance(entries, list) or not all(isinstance(entry, dict) for entry in entries):
                continue
            labels = list(dict.fromkeys(entry["name"] for entry in entries if isinstance(entry.get("name"), str)))
            current = getattr(incident, field) or ""
            parts = [part.strip() for part in current.split(",") if part.strip()]
            # Edited records use their current values, not an outdated workbook snapshot.
            if labels and (current == ", ".join(labels) or parts == [entry.get("source") for entry in entries]):
                country_facets[camel_case(field)] = labels
    if country_facets:
        data["countryFacets"] = country_facets
    if details:
        data["rawFields"] = incident.raw_fields
        updates = raw.get("_incidentUpdates")
        if isinstance(updates, list):
            fields = ("candidateId", "publishedAt", "reportedAt", "summary", "sourceUrls")
            data["updates"] = [{key: entry.get(key) for key in fields} for entry in updates if isinstance(entry, dict)]
        data["sources"] = [{"url": x.url, "shortName": x.short_name, "rawText": x.raw_text} for x in incident.sources]
        data["organisations"] = [{"name": x.name, "country": x.country, "role": x.role} for x in incident.organisations]
        data["actors"] = [{"name": x.name, "country": x.country} for x in incident.actors]
        data["locations"] = [{"name": x.name, "country": x.country, "latitude": x.latitude, "longitude": x.longitude, "precision": x.precision, "source": x.source} for x in incident.locations]
        data["provenance"] = [{"importBatchId": x.import_batch_id, "sourceRow": x.source_row} for x in incident.provenance]
    return data


def revision_dict(revision):
    before, after = revision.before_snapshot, revision.after_snapshot
    changes = []
    for field in dict.fromkeys([*before, *after]):
        if field in {"updatedAt", "sources", "rawFields", "provenance", "description"}:
            continue
        if before.get(field) != after.get(field):
            changes.append({"field": field, "before": before.get(field), "after": after.get(field)})
    if before.get("description") != after.get("description") and not any(
        change["field"] == "summary" for change in changes
    ):
        changes.append({
            "field": "description", "before": before.get("description"), "after": after.get("description"),
        })
    old_urls = {source["url"] for source in before.get("sources", [])}
    old_urls.add(before.get("sourceUrl"))
    return {
        "id": revision.id,
        "createdAt": iso(revision.created_at),
        "reason": revision.reason,
        "changes": changes,
        "addedSources": [source["url"] for source in after.get("sources", []) if source["url"] not in old_urls],
    }


def candidate_dict(candidate):
    from .editorial import candidate_review_metadata

    return {
        "id": candidate.id,
        "title": candidate.title,
        "sourceUrl": candidate.source_url,
        "summary": candidate.summary,
        "matchedKeywords": candidate.matched_keywords,
        "status": candidate.status,
        "baseline": candidate.baseline,
        "firstSeenAt": iso(candidate.first_seen_at),
        "incidentId": candidate.incident_id,
        "rejectionReason": candidate.rejection_reason,
        "rejectionNotes": candidate.rejection_notes,
        "origin": getattr(candidate, "origin", "rss"),
        "proposedFields": getattr(candidate, "proposed_fields", None) or {},
        "evidence": getattr(candidate, "evidence", None) or {},
        "verificationStatus": getattr(candidate, "verification_status", "unreviewed"),
        "aiRunId": getattr(candidate, "ai_run_id", None),
        **candidate_review_metadata(candidate),
        "relationshipType": candidate.relationship_type,
        "relatedIncidentId": candidate.related_incident_id,
        "relationshipReason": candidate.relationship_reason,
    }

