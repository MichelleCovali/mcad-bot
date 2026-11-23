"""Publish or reject a report inside the caller's database transaction."""
import ipaddress
import json
from urllib.parse import urlsplit

from fastapi import HTTPException
from sqlalchemy import select, update

from .models import Candidate, Incident, IncidentRevision, IncidentSource, utcnow
from .service import IncidentAlreadyExists, ReviewConflict, incident_dict, iso, merge_incident_sources, normalize_payload, safe_url, update_incident, upsert_incident


REJECTION_REASONS = {
    "not_maritime", "not_cyber", "duplicate", "insufficient_evidence",
    "incorrect_information", "old_news", "other",
}


def _publisher(url):
    """Conservatively group publisher subdomains and common country suffixes."""
    host = (urlsplit(url).hostname or "").lower().rstrip(".")
    if not host or host.endswith((".localhost", ".local", ".internal")):
        return None
    try:
        ipaddress.ip_address(host)
    except ValueError:
        try:
            labels = host.encode("idna").decode("ascii").split(".")
        except UnicodeError:
            return None
        if len(labels) < 2 or any(not label or label.startswith("-") or label.endswith("-") or
                                  not label.replace("-", "").isalnum() for label in labels):
            return None
        country_suffix = len(labels[-1]) == 2 and labels[-2] in {"ac", "co", "com", "edu", "gov", "net", "org"}
        if country_suffix and len(labels) < 3:
            return None
        return ".".join(labels[-3:] if country_suffix else labels[-2:])
    return None


def _reference_key(url):
    from .ai_monitor import _url_identity

    # Aliases such as http://, www and tracking parameters do not turn a
    # previously reviewed page into a separate source with no evidence history.
    parts = urlsplit(_url_identity(url))
    return (parts.hostname or "").removeprefix("www.").rstrip("."), parts.path.rstrip("/"), parts.query


def _submitted_sources(values):
    """Match the sources the incident writer will actually keep."""
    sources = {}
    for source in values.get("sources", []):
        if not isinstance(source, dict):
            raise ValueError("sources must contain objects")
        url = safe_url(source.get("url"))
        if url:
            sources.setdefault(url, source)
    for value in [values.get("source_url"), *values.get("reference_urls", [])]:
        url = safe_url(value)
        if url:
            sources.setdefault(url, {"url": url})
    return sources


def _evidence(candidate):
    evidence = candidate.evidence if candidate is not None else {}
    return evidence if isinstance(evidence, dict) else {}


def _material_update(candidate):
    value = _evidence(candidate).get("materialUpdate")
    return value if isinstance(value, dict) and value else None


def _suggested_incident(candidate):
    value = _evidence(candidate).get("suggestedIncidentId")
    return value if isinstance(value, str) and value else None


def _qualified_origins(urls, candidate=None, *, telegram=False):
    evidence = _evidence(candidate)
    known = {}
    for source in evidence.get("sources") or []:
        if not isinstance(source, dict) or not isinstance(source.get("url"), str):
            continue
        try:
            url = safe_url(source["url"])
        except ValueError:
            continue
        if url:
            known.setdefault(_reference_key(url), []).append(source)

    material = _material_update(candidate)
    update_references = None
    if material:
        update_references = {_reference_key(url) for url in material.get("supportingUrls") or [] if isinstance(url, str)}

    for url in urls:
        publisher = _publisher(url)
        if not publisher:
            continue
        key = _reference_key(url)
        source_records = known.get(key)
        if source_records is None:
            if telegram:
                continue
            # A web reviewer may supply a new citation after checking the claims.
            group = "publisher:" + publisher
        else:
            # Canonical URL aliases still describe one page. A later duplicate
            # must not erase an earlier contradiction or change its origin.
            if update_references is not None and key not in update_references:
                continue
            origins = set()
            for source in source_records:
                claims, origin = source.get("supports"), source.get("independenceGroup")
                if (source.get("contradicts") or not isinstance(claims, list) or
                        not any(isinstance(claim, str) and claim.strip() for claim in claims) or
                        source.get("openedByVerifier") is not True or
                        not isinstance(origin, str) or not origin.strip()):
                    break
                origins.add(" ".join(origin.casefold().split()))
            else:
                if len(origins) == 1:
                    yield url, publisher, "origin:" + next(iter(origins))
                continue
            continue
        yield url, publisher, group


def _origin_count(urls, candidate=None):
    """Count distinct publishers and reporting origins among retained sources."""
    publishers, groups = set(), set()
    for _url, publisher, group in _qualified_origins(urls, candidate):
        publishers.add(publisher)
        groups.add(group)
    return min(len(publishers), len(groups))


def _proposed_values(candidate):
    values = normalize_payload({**(candidate.proposed_fields or {}), "title": candidate.title})
    values.setdefault("source_url", candidate.source_url)
    values.setdefault("summary", candidate.summary)
    return values


def telegram_corroborating_urls(candidate, *, url_filter=None):
    """Return a checked pair spanning different publishers and reporting origins."""
    evidence = _evidence(candidate)
    material, suggested = _material_update(candidate), _suggested_incident(candidate)
    if (candidate.status != "pending" or candidate.origin != "ai" or candidate.verification_status != "supported" or
            bool(material) != bool(suggested) or evidence.get("contradictions") or
            any(isinstance(source, dict) and source.get("contradicts") for source in evidence.get("sources") or [])):
        return ()
    if material and (not isinstance(material.get("summary"), str) or not material["summary"].strip()):
        return ()
    try:
        sources = _submitted_sources(_proposed_values(candidate))
        if url_filter:
            sources = [url for url in sources if url_filter(url)]
        distinct = list(_qualified_origins(sources, candidate, telegram=True))
    except ValueError:
        return ()
    for index, (first, first_publisher, first_group) in enumerate(distinct):
        for second, second_publisher, second_group in distinct[index + 1:]:
            if first_publisher != second_publisher and first_group != second_group:
                return first, second
    return ()


def can_approve_from_telegram(candidate):
    return bool(telegram_corroborating_urls(candidate))


def candidate_review_metadata(candidate):
    try:
        origins = _origin_count(_submitted_sources(_proposed_values(candidate)), candidate)
    except ValueError:
        origins = 0
    return {"suggestedIncidentId": _suggested_incident(candidate),
            "isPotentialUpdate": bool(_material_update(candidate) or _suggested_incident(candidate)),
            "independentSourceCount": origins,
            "telegramApprovalEligible": can_approve_from_telegram(candidate)}


def _require_sources(sources, confirmed, candidate=None):
    if _origin_count(sources, candidate) < 2:
        raise HTTPException(422, "Provide two independent sources that support this report or update.")
    if confirmed is not True:
        raise HTTPException(422, "Confirm that two independent sources support the report or update.")


def create_manual_incident(session, payload):
    values = dict(payload)
    confirmed = values.pop("independentSourcesConfirmed", False)
    values = normalize_payload(values)
    if values.get("status", "published") == "published":
        _require_sources(_submitted_sources(values), confirmed)
    return upsert_incident(session, values, notify=True)


def edit_manual_incident(session, incident, payload):
    values = dict(payload)
    confirmed = values.pop("independentSourcesConfirmed", False)
    values = normalize_payload(values, existing=incident)
    source_fields = {"source_url", "reference_urls", "sources"}
    factual_changes = {key for key, value in values.items()
                       if key not in source_fields | {"status", "raw_fields"}
                       and getattr(incident, key) != value}
    if values.get("status", incident.status) == "published" and (
            incident.status != "published" or factual_changes or values.keys() & source_fields):
        citations = {"source_url": incident.source_url, **values}
        if not values.keys() & {"reference_urls", "sources"}:
            citations["reference_urls"] = [source.url for source in incident.sources]
        _require_sources(_submitted_sources(citations), confirmed)
    return update_incident(session, incident, values)


def _load_candidate(session, candidate_id):
    candidate = session.scalar(select(Candidate).where(Candidate.id == candidate_id).with_for_update())
    if candidate is None:
        raise HTTPException(404, "Candidate not found.")
    return candidate


def _transition(session, candidate, status, **fields):
    if candidate.status != "pending":
        raise HTTPException(409, "This report has already been reviewed.")
    changed = session.execute(update(Candidate).where(
        Candidate.id == candidate.id, Candidate.status == "pending",
    ).values(status=status, **fields).execution_options(synchronize_session=False))
    if changed.rowcount != 1:
        raise HTTPException(409, "This report has already been reviewed.")
    candidate.status = status
    for key, value in fields.items():
        setattr(candidate, key, value)


def _ai_review(candidate):
    return {"candidateId": candidate.id, "runId": candidate.ai_run_id,
            "verificationStatus": candidate.verification_status, "evidence": _evidence(candidate),
            "proposedFields": candidate.proposed_fields or {}}


def _append_incident_update(session, candidate, target_id, values, sources, *, via_telegram):
    incident = session.scalar(select(Incident).where(Incident.id == target_id).with_for_update())
    if not incident or incident.status != "published":
        raise HTTPException(422, "Choose an existing published incident for this update.")

    note = (values.get("summary") or values.get("description") or "").strip()
    if not note:
        raise HTTPException(422, "Describe the confirmed new development before applying the update.")
    raw = dict(incident.raw_fields or {})
    updates = raw.get("_incidentUpdates")
    updates = list(updates) if isinstance(updates, list) else []
    supporting_urls = [url for url, _, _ in _qualified_origins(sources, candidate, telegram=via_telegram)]
    if any(isinstance(item, dict) and item.get("summary") == note and
           isinstance(item.get("sourceUrls"), list) and
           {url for url in item["sourceUrls"] if isinstance(url, str)} == set(supporting_urls) for item in updates):
        raise HTTPException(409, "This development is already recorded on the incident.")
    material = _material_update(candidate) or {}
    entry = {"candidateId": candidate.id, "publishedAt": iso(utcnow()),
             "reportedAt": material.get("reportedAt"), "summary": note, "sourceUrls": supporting_urls}
    if candidate.origin == "ai":
        entry["aiReview"] = _ai_review(candidate)
    raw["_incidentUpdates"] = [*updates, entry]

    before = json.loads(json.dumps(incident_dict(incident, details=True)))
    recorded_urls = {source.url for source in incident.sources}
    retained = merge_incident_sources(incident, [
        {**source, "url": url} for url, source in sources.items()
    ])
    incident.sources.extend(IncidentSource(**source) for source in retained
                            if source["url"] not in recorded_urls)
    update_incident(session, incident, {"raw_fields": raw})
    session.add(IncidentRevision(
        incident_id=incident.id, candidate_id=candidate.id,
        reason="Corroborated development approved in Telegram." if via_telegram
               else "Corroborated development approved in the review workspace.",
        before_snapshot=before,
        after_snapshot=json.loads(json.dumps(incident_dict(incident, details=True))),
        evidence={"reviewedUpdateSources": supporting_urls, "reportEvidence": _evidence(candidate)},
    ))
    return incident


def approve_candidate(session, candidate_id, payload, *, via_telegram=False):
    candidate = _load_candidate(session, candidate_id)
    if candidate.status == "approved" and candidate.incident_id:
        if candidate.relationship_type == "update" and not via_telegram and not payload.get("updateIncidentId"):
            raise ReviewConflict("report_resolved", "This report was already applied as an update to an existing incident.")
        return session.get(Incident, candidate.incident_id)
    if candidate.status != "pending":
        if via_telegram:
            raise HTTPException(409, "This report has already been reviewed.")
        raise ReviewConflict("report_resolved", "This report has already been reviewed.")

    content = dict(payload)
    target_id = content.pop("updateIncidentId", None)
    if target_id is not None and (not isinstance(target_id, str) or not target_id or len(target_id) > 36):
        raise HTTPException(422, "Choose an existing published incident for this update.")
    confirmed = content.pop("independentSourcesConfirmed", False)
    distinct = content.pop("distinctIncidentConfirmed", False)
    suggested = _suggested_incident(candidate)
    material = _material_update(candidate)
    needs_relationship_review = (
        candidate.relationship_type in {"possible_update", "possible_duplicate"}
        or candidate.related_incident_id is not None
    )
    if via_telegram:
        if not can_approve_from_telegram(candidate) or target_id != suggested:
            raise HTTPException(422, "Confirm the sources and incident in the web review queue before publishing.")
        values = _proposed_values(candidate)
        if material:
            values["summary"] = material.get("summary") or ""
    else:
        if needs_relationship_review and not target_id:
            raise HTTPException(409, "Review the suggested relationship before publishing a separate incident.")
        if (suggested or material) and not target_id and distinct is not True:
            raise HTTPException(422, "Choose the existing incident or confirm that this is a separate event.")
        values = normalize_payload(content)
        values.setdefault("source_url", candidate.source_url)
    values.setdefault("description", candidate.summary)
    sources = _submitted_sources(values)
    if not via_telegram:
        _require_sources(sources, confirmed, candidate)
    _transition(session, candidate, "approved")

    if target_id:
        incident = _append_incident_update(session, candidate, target_id, values, sources, via_telegram=via_telegram)
        candidate.relationship_type = "update"
        candidate.related_incident_id = incident.id
    else:
        values["status"] = "published"
        if candidate.origin == "ai":
            values["raw_fields"] = {**values.get("raw_fields", {}), "_aiReview": _ai_review(candidate)}
        try:
            incident, _ = upsert_incident(session, values, notify=True,
                                          reviewed_candidate_id=candidate.id, require_new=True)
        except IncidentAlreadyExists as error:
            raise HTTPException(409, str(error)) from None
    candidate.incident_id = incident.id
    session.flush()
    return incident


def reject_candidate(session, candidate_id, reason, notes):
    if not isinstance(reason, str) or reason not in REJECTION_REASONS:
        raise HTTPException(422, "A valid rejection reason is required.")
    if not isinstance(notes, str) or not notes.strip() or len(notes) > 2000:
        raise HTTPException(422, "Explain the rejection in 1 to 2,000 characters.")
    candidate = _load_candidate(session, candidate_id)
    _transition(session, candidate, "rejected", rejection_reason=reason, rejection_notes=notes.strip())
    from .prompt_learning import record_editorial_rejection

    record_editorial_rejection(session, candidate)
    return candidate
