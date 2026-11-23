"""Bounded corroboration rechecks that cannot replace a reviewer's decision.

These helpers never contact a provider or enqueue notifications. Selection and
binding supply locally trusted identities; provider output supplies no write ID.
"""
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from hashlib import sha256
import json
import unicodedata
from urllib.parse import urlsplit

from sqlalchemy import select

from .models import Candidate


RECHECK_INTERVAL = timedelta(hours=6)
MANUAL_MARKERS = {
    "classificationReviews", "relationshipReviews", "reviewedUpdateSources",
    "independentSourcesConfirmed", "reviewerConfirmed", "manuallyReviewed",
    "manualReview", "humanReviewed", "reviewedAt", "reviewedBy",
}
RELATIONSHIP_EVIDENCE = {"suggestedIncidentId", "matchReason", "relationshipReason"}


def _utc(value):
    if isinstance(value, str):
        try:
            value = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return None
    if not isinstance(value, datetime):
        return None
    return value.astimezone(timezone.utc).replace(tzinfo=None) if value.tzinfo else value


def _iso(value):
    return _utc(value).isoformat(timespec="seconds") + "Z"


def _title(value):
    return " ".join(unicodedata.normalize("NFKC", str(value or "")).casefold().split())


def _url_key(value):
    # Import lazily: the monitor calls these helpers after its module is loaded.
    from .ai_monitor import _public_url, _url_identity

    try:
        parts = urlsplit(_url_identity(_public_url(value)))
        port = parts.port
    except (TypeError, ValueError):
        return None
    if port == (443 if parts.scheme == "https" else 80):
        port = None
    return ((parts.hostname or "").removeprefix("www.").rstrip("."), port,
            parts.path.rstrip("/") or "/", parts.query)


def _source_urls(candidate):
    fields, evidence = candidate.proposed_fields or {}, candidate.evidence or {}
    def entries(value):
        return value if isinstance(value, list) else []
    values = [candidate.source_url, fields.get("sourceUrl"), *entries(fields.get("referenceUrls"))]
    for source in [*entries(fields.get("sources")), *entries(evidence.get("sources"))]:
        if isinstance(source, dict):
            values.append(source.get("url"))
    result, seen = [], set()
    for value in values:
        key = _url_key(value)
        if key and key not in seen:
            result.append(value)
            seen.add(key)
    return result[:10]


def _manual(candidate):
    evidence = candidate.evidence or {}
    fields = candidate.proposed_fields or {}
    return any(evidence.get(key) or fields.get(key) for key in MANUAL_MARKERS)


def _snapshot(candidate):
    values = {column.name: deepcopy(getattr(candidate, column.name))
              for column in Candidate.__table__.columns}
    # Attempt bookkeeping is not a human content change. Recording an attempt
    # before the paid boundary must not invalidate the selected content snapshot.
    values["evidence"] = dict(values.get("evidence") or {})
    values["evidence"].pop("recheckAttemptAt", None)
    return sha256(json.dumps(values, sort_keys=True, separators=(",", ":"),
                             default=str, ensure_ascii=False).encode()).hexdigest()


def _eligible(candidate):
    return bool(candidate and candidate.status == "pending" and candidate.origin == "ai"
                and not candidate.baseline and not candidate.incident_id
                and not candidate.rejection_reason and not candidate.rejection_notes
                and candidate.verification_status in {"needs_review", "contradicted"}
                and not _manual(candidate))


def _recent(candidate, now, window_start):
    evidence = candidate.evidence or {}
    development = evidence.get("materialUpdate")
    raw = development.get("reportedAt") if isinstance(development, dict) else evidence.get("firstReportedAt")
    reported = _utc(raw)
    if reported is None:
        seen = _utc(candidate.first_seen_at)
        return bool(seen and window_start <= seen <= now), True
    if reported > now + timedelta(minutes=15):
        return False, False
    if reported >= window_start:
        return True, False
    # A day-only date spanning the cutoff is plausible but still unconfirmed.
    if isinstance(raw, str) and len(raw) == 10 and reported + timedelta(days=1) > window_start:
        return True, True
    return False, False


def select_followups(factory, now, window_start, limit):
    """Select recent unresolved AI drafts without treating them as new reports."""
    now, window_start = _utc(now), _utc(window_start)
    if type(limit) is not int or limit <= 0 or not now or not window_start:
        return []
    result = []
    with factory() as session:
        candidates = session.scalars(select(Candidate).where(
            Candidate.status == "pending", Candidate.origin == "ai", Candidate.baseline.is_(False),
            Candidate.verification_status.in_(["needs_review", "contradicted"]),
        ).order_by(Candidate.first_seen_at, Candidate.id))
        for candidate in candidates:
            if not _eligible(candidate):
                continue
            recent, unknown_dates = _recent(candidate, now, window_start)
            evidence = candidate.evidence or {}
            checks = [_utc(evidence.get(key)) for key in ("checkedAt", "recheckAttemptAt")]
            checks = [value for value in checks if value is not None]
            checked = max(checks) if checks else _utc(candidate.first_seen_at)
            if not recent or (checked and now - checked < RECHECK_INTERVAL):
                continue
            urls = _source_urls(candidate)
            if not urls or not _title(candidate.title):
                continue
            development = evidence.get("materialUpdate")
            if isinstance(development, dict):
                development = {"reportedAt": development.get("reportedAt"),
                               "summary": development.get("summary", "")}
            else:
                development = None
            summary = (candidate.summary or "")[:11000]
            if unknown_dates:
                summary += " Reporting freshness is unconfirmed; verify the source dates before treating this as recent news."
            result.append((checked or datetime.min, _utc(candidate.first_seen_at) or datetime.min,
                candidate.id, {"title": candidate.title, "summary": summary, "sourceUrls": urls,
                "eventDate": evidence.get("eventDate"), "firstReportedAt": evidence.get("firstReportedAt"),
                "development": development, "followupCandidateId": candidate.id,
                "_followupSnapshot": _snapshot(candidate),
                "_followupYear": (candidate.proposed_fields or {}).get("year"),
                "_followupNeedsDateVerification": unknown_dates}))
    # A six-hour cooldown alone cannot rotate drafts on an eight-hour schedule:
    # the oldest-created draft would become eligible first every time. Prefer
    # the least recently checked draft so each unresolved lead gets a turn.
    return [entry[3] for entry in sorted(result, key=lambda entry: entry[:3])[:limit]]


def _compatible_dates(proposal, selected):
    evidence, fields = proposal.get("evidence") or {}, proposal.get("proposed_fields") or {}
    previous, current = _utc(selected.get("eventDate")), _utc(evidence.get("eventDate"))
    if previous and current and previous.date() != current.date():
        return False
    old_year = selected.get("_followupYear")
    old_year = old_year if type(old_year) is int else previous.year if previous else None
    year = fields.get("year")
    year = year if type(year) is int else current.year if current else None
    if old_year is not None and year is not None and old_year != year:
        return False
    old_update, update = selected.get("development"), evidence.get("materialUpdate")
    if bool(old_update) != bool(update):
        return False
    if old_update and update:
        previous, current = _utc(old_update.get("reportedAt")), _utc(update.get("reportedAt"))
        if previous and current and previous.date() != current.date():
            return False
    return True


def bind_followups(proposals, selected):
    """Bind provider-independent write identities only for unique safe matches."""
    for proposal in proposals:
        proposal.pop("_followupCandidateId", None)
        proposal.pop("_followupSnapshot", None)
        proposal.pop("_followupUnmatched", None)
        proposal.pop("followupCandidateId", None)
        fields, evidence = proposal.get("proposed_fields") or {}, proposal.get("evidence") or {}
        values = [proposal.get("source_url"), fields.get("sourceUrl"), *fields.get("referenceUrls", [])]
        values.extend(source.get("url") for source in evidence.get("sources", []) if isinstance(source, dict))
        keys = {key for value in values if (key := _url_key(value))}
        matches = {entry["followupCandidateId"]: entry for entry in selected
            if _title(entry.get("title")) == _title(proposal.get("title"))
            and keys.intersection(_url_key(value) for value in entry.get("sourceUrls", []))
            and _compatible_dates(proposal, entry)}
        if len(matches) == 1:
            entry = next(iter(matches.values()))
            proposal["_followupCandidateId"] = entry["followupCandidateId"]
            proposal["_followupSnapshot"] = entry["_followupSnapshot"]
        elif any(keys.intersection(_url_key(value) for value in entry.get("sourceUrls", []))
                 or (_title(entry.get("title")) == _title(proposal.get("title"))
                     and _compatible_dates(proposal, entry)) for entry in selected):
            # An altered title, date or ambiguous identity cannot authorize a
            # write to the old draft. It also cannot safely become a new draft
            # just because the verifier selected a new primary citation.
            proposal["_followupUnmatched"] = True
    return proposals


def record_followup_attempts(factory, selected, now):
    """Apply the cooldown even if the provider later fails or returns no report."""
    updated = 0
    with factory.begin() as session:
        for entry in selected:
            candidate = session.scalar(select(Candidate).where(
                Candidate.id == entry.get("followupCandidateId")
            ).with_for_update().execution_options(populate_existing=True))
            if not _eligible(candidate) or _snapshot(candidate) != entry.get("_followupSnapshot"):
                continue
            candidate.evidence = {**(candidate.evidence or {}), "recheckAttemptAt": _iso(now)}
            updated += 1
    return updated


def persist_followup(session, proposal, run_id, now):
    """Refresh the same untouched AI draft, or consume a stale followup safely."""
    candidate_id = proposal.get("_followupCandidateId")
    snapshot = proposal.get("_followupSnapshot")
    if not candidate_id:
        return "not_followup"
    candidate = session.scalar(select(Candidate).where(Candidate.id == candidate_id)
        .with_for_update().execution_options(populate_existing=True))
    if not _eligible(candidate) or _snapshot(candidate) != snapshot:
        return "stale"
    if (proposal.get("verification_status") not in {"supported", "needs_review", "contradicted"}
            or not isinstance(proposal.get("evidence"), dict)
            or not isinstance(proposal.get("proposed_fields"), dict)):
        return "stale"
    previous = deepcopy(candidate.evidence or {})
    history = previous.pop("researchRechecks", [])
    history = list(history) if isinstance(history, list) else []
    history.append({"runId": run_id, "recheckedAt": _iso(now), "previousAiRunId": candidate.ai_run_id,
        "previousTitle": candidate.title, "previousSummary": candidate.summary,
        "previousVerificationStatus": candidate.verification_status,
        "previousFields": deepcopy(candidate.proposed_fields or {}), "previousEvidence": previous})
    evidence = deepcopy(proposal["evidence"])
    for key in RELATIONSHIP_EVIDENCE:
        evidence.pop(key, None)
        if key in previous:
            evidence[key] = previous[key]
    evidence.pop("proposedRelatedIncidentId", None)
    evidence.update(researchRechecks=history, recheckAttemptAt=_iso(now), checkedAt=_iso(now))
    candidate.title = proposal["title"]
    candidate.summary = proposal.get("summary") or ""
    candidate.proposed_fields = deepcopy(proposal["proposed_fields"])
    candidate.evidence = evidence
    candidate.verification_status = proposal["verification_status"]
    candidate.ai_run_id = run_id
    # Keep primary source identity, relationship decisions, first_seen_at and all
    # notification rows. No alert is queued for this refreshed pending report.
    return "updated"
