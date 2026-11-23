"""Audit research attempts; a completed pass never establishes exhaustive recall."""
from sqlalchemy import func, select

from .models import Candidate
from .service import iso


MARITIME_AREAS = [
    "Ports, terminals, maritime logistics and their suppliers: ransomware, intrusion, DDoS and disruption",
    "Vessels, onboard IT/OT, shipping companies, shipyards, maritime authorities and naval organisations",
    "Maritime AIS/GNSS/GPS spoofing or jamming, satellite communications and tracking interference",
    "Offshore and undersea infrastructure and suppliers with an evidenced maritime connection",
]
REGIONS = ["Europe and Baltic", "Middle East and Red Sea", "Asia and Pacific", "Americas and Africa"]


def tool_budgets(total):
    if total < 6:
        return 1, 0, total - 1
    discovery = max(2, total // 3)
    return (discovery + 1) // 2, discovery // 2, total - discovery


def new_coverage(now, window_start, budgets):
    return {"version": 1, "scope": "maritime_cybersecurity", "timezone": "UTC",
        "windowStart": iso(window_start), "windowEnd": iso(now),
        "completenessGuaranteed": False, "requestedAreas": MARITIME_AREAS, "requestedRegions": REGIONS,
        "passes": [{"name": name, "status": "pending" if budget else "skipped_budget",
            "maxToolCalls": budget, "queries": [], "leadsFound": 0, "sourceCount": 0}
            for name, budget in zip(("primary", "gap_check"), budgets[:2])],
        "verification": {"status": "pending", "leadsSelected": 0, "leadsDeferred": 0,
            "recheckedReports": 0, "updatedReports": 0},
        "limitations": [] if budgets[1] else ["Tool budget is too small for a complementary search."]}


def trace_queries(response):
    queries = []
    for item in response.get("output", []):
        if item.get("type") != "web_search_call" or item.get("status", "completed") != "completed":
            continue
        action = item.get("action") or {}
        if action.get("type") != "search":
            continue
        values = action.get("queries", [])
        values = values if isinstance(values, list) else []
        values = [action.get("query"), *values]
        for value in values:
            if isinstance(value, str) and value.strip():
                clean = " ".join(value.split())[:500]
                if clean not in queries:
                    queries.append(clean)
    return queries[:80]


def coverage_today(session, runs, now):
    day_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    audited = [run for run in runs if run.coverage and run.coverage.get("version") == 1]
    checked = [run for run in audited if run.status == "completed"
        and all(item["status"] == "completed" for item in run.coverage.get("passes", []))
        and len(run.coverage.get("passes", [])) == 2
        and run.coverage.get("verification", {}).get("status") in {"completed", "not_needed"}
        and not run.coverage.get("limitations")]
    queries = list(dict.fromkeys(query for run in audited
        for item in run.coverage.get("passes", []) for query in item.get("queries", [])))
    unresolved = session.scalar(select(func.count()).select_from(Candidate).where(
        Candidate.status == "pending", Candidate.origin == "ai", Candidate.baseline.is_(False),
        Candidate.verification_status != "supported"))
    confirmed = session.scalar(select(func.count()).select_from(Candidate).where(
        Candidate.origin == "ai", Candidate.verification_status == "supported",
        Candidate.status.in_(["pending", "approved"]), Candidate.ai_run_id.in_([run.id for run in runs]))) if runs else 0
    return {"dateUTC": day_start.date().isoformat(), "windowStart": iso(day_start), "windowEnd": iso(now),
        "sweepRunsCompleted": len(checked), "runsIncomplete": len(audited) - len(checked),
        "runsWithoutAudit": len(runs) - len(audited), "pendingVerification": unresolved,
        "confirmedReports": confirmed, "queries": queries[:200],
        "latestCheckedAt": iso(max((run.finished_at for run in checked if run.finished_at), default=None)),
        "completenessGuaranteed": False}
