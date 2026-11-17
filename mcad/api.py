"""Same-origin MCAD API and frontend hosting. Writes always require a server token."""
import hmac
from contextlib import asynccontextmanager
from pathlib import Path
from tempfile import TemporaryDirectory
from zipfile import BadZipFile, LargeZipFile
from xml.etree.ElementTree import ParseError

from fastapi import Body, Depends, FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from starlette.concurrency import run_in_threadpool
from openpyxl.utils.exceptions import InvalidFileException
from fastapi.middleware.gzip import GZipMiddleware
from sqlalchemy import func, select, text, update
from sqlalchemy.exc import IntegrityError, SQLAlchemyError

from .config import Settings
from .db import get_session_factory, make_engine
from .models import Candidate, CandidateDelivery, Delivery, ImportBatch, Incident, IncidentSource, Subscriber, utcnow
from .service import candidate_dict, incident_dict, iso, safe_url, snake_case, update_incident, upsert_incident
from .security import ProtectedRequestsMiddleware


def create_app(settings=None, engine=None):
    settings = settings or Settings.from_env()
    engine = engine or make_engine(settings.database_url)
    factory = get_session_factory(engine)

    @asynccontextmanager
    async def lifespan(application):
        from .workers import WorkerController, seed_subscriber
        # Migrations are explicit at deployment, never silently mutate schemas here.
        if settings.telegram_chat_id:
            with factory.begin() as session:
                seed_subscriber(session, settings.telegram_chat_id)
        worker = WorkerController(factory, settings) if settings.enable_workers else None
        application.state.worker = worker
        if worker:
            worker.start()
        try:
            yield
        finally:
            if worker:
                worker.stop()

    app = FastAPI(title="MCAD API", version="1.0.0", lifespan=lifespan)
    app.add_middleware(GZipMiddleware, minimum_size=1000)
    app.add_middleware(ProtectedRequestsMiddleware, admin_token=settings.admin_api_token)
    app.state.settings, app.state.engine, app.state.session_factory = settings, engine, factory
    if settings.cors_origins:
        app.add_middleware(CORSMiddleware, allow_origins=list(settings.cors_origins), allow_methods=["GET", "POST", "PATCH"], allow_headers=["Authorization", "Content-Type"])

    def session_dependency():
        with factory() as session:
            yield session

    def require_admin(request: Request):
        if not settings.admin_api_token:
            raise HTTPException(503, "Administration is not configured on this server.")
        scheme, _, token = request.headers.get("authorization", "").partition(" ")
        if scheme.lower() != "bearer" or not hmac.compare_digest(token.encode("utf-8"), settings.admin_api_token.encode("utf-8")):
            raise HTTPException(401, "A valid administrator bearer token is required.", headers={"WWW-Authenticate": "Bearer"})

    @app.exception_handler(ValueError)
    async def invalid_value(_request, error):
        return JSONResponse(status_code=422, content={"detail": str(error)})

    @app.exception_handler(IntegrityError)
    async def conflicting_record(_request, _error):
        return JSONResponse(status_code=409, content={"detail": "The record conflicts with an existing source identifier."})

    @app.exception_handler(SQLAlchemyError)
    async def database_unavailable(_request, _error):
        # Database URLs, bound values and raw driver errors must never reach users/logs.
        return JSONResponse(status_code=503, content={"detail": "The database is temporarily unavailable."})

    @app.get("/api/health")
    def health(session=Depends(session_dependency)):
        session.execute(text("SELECT 1"))
        total = session.scalar(select(func.count()).select_from(Incident).where(Incident.status == "published"))
        mapped = session.scalar(select(func.count()).select_from(Incident).where(Incident.status == "published", Incident.latitude.is_not(None), Incident.longitude.is_not(None)))
        return {
            "status": "ok", "database": "connected",
            "telegram": {"configured": bool(settings.telegram_bot_token),
                         "pollingEnabled": bool(settings.enable_workers and settings.enable_telegram_polling and settings.telegram_bot_token),
                         "subscriberCount": session.scalar(select(func.count()).select_from(Subscriber).where(Subscriber.active.is_(True))),
                         "pendingDeliveries": session.scalar(select(func.count()).select_from(Delivery).where(Delivery.status.in_(["pending", "retry", "sending"])))
                             + session.scalar(select(func.count()).select_from(CandidateDelivery).where(CandidateDelivery.status.in_(["pending", "retry", "sending"])))},
            "feed": {"enabled": bool(settings.enable_workers and settings.enable_feed_polling),
                     "pendingCandidates": session.scalar(select(func.count()).select_from(Candidate).where(Candidate.status == "pending"))},
            "incidents": {"total": total, "mapped": mapped, "unmapped": total - mapped},
            "imports": {"batches": session.scalar(select(func.count()).select_from(ImportBatch)),
                        "lastImportedAt": iso(session.scalar(select(func.max(ImportBatch.created_at))))},
            "ai": {"serverEnabled": settings.enable_ai_search,
                   "configured": bool(settings.openai_api_key),
                   "automaticPublication": False},
        }

    @app.get("/api/incidents")
    def list_incidents(session=Depends(session_dependency)):
        records = list(session.scalars(select(Incident).where(Incident.status == "published").order_by(Incident.year.desc().nullslast(), Incident.created_at.desc(), Incident.id)))
        mapped = sum(record.latitude is not None and record.longitude is not None for record in records)
        return {"items": [incident_dict(record) for record in records], "meta": {
            "total": len(records), "mapped": mapped, "unmapped": len(records) - mapped,
            "lastUpdated": iso(max((record.updated_at for record in records), default=None)),
        }}

    @app.get("/api/incidents/{incident_id}")
    def get_incident(incident_id: str, session=Depends(session_dependency)):
        record = session.get(Incident, incident_id)
        if record is None or record.status != "published":
            raise HTTPException(404, "Incident not found.")
        return incident_dict(record, details=True)

    @app.post("/api/incidents", dependencies=[Depends(require_admin)])
    def create_incident(payload: dict = Body(...), session=Depends(session_dependency)):
        with session.begin():
            incident, created = upsert_incident(session, payload, notify=True)
            result = incident_dict(incident, details=True)
        return JSONResponse(result, status_code=201 if created else 200)

    @app.patch("/api/incidents/{incident_id}", dependencies=[Depends(require_admin)])
    def patch_incident(incident_id: str, payload: dict = Body(...), session=Depends(session_dependency)):
        with session.begin():
            incident = session.scalar(select(Incident).where(Incident.id == incident_id).with_for_update())
            if incident is None:
                raise HTTPException(404, "Incident not found.")
            update_incident(session, incident, payload)
            result = incident_dict(incident, details=True)
        return result

    @app.get("/api/admin/candidates", dependencies=[Depends(require_admin)])
    def list_candidates(status: str = "pending", session=Depends(session_dependency)):
        if status not in {"pending", "approved", "rejected", "all"}:
            raise HTTPException(422, "status must be pending, approved, rejected or all")
        query = select(Candidate).order_by(Candidate.first_seen_at.desc())
        if status != "all":
            query = query.where(Candidate.status == status)
        candidates = list(session.scalars(query))
        return {"items": [candidate_dict(candidate) for candidate in candidates], "meta": {"total": len(candidates)}}

    @app.get("/api/admin/catalogs", dependencies=[Depends(require_admin)])
    def workbook_catalogs(session=Depends(session_dependency)):
        from .import_workbook import SOURCE_FIELDS
        from .service import camel_case
        batch = session.scalar(select(ImportBatch).order_by(ImportBatch.created_at.desc(), ImportBatch.id).limit(1))
        catalogs = (batch.metadata_json or {}).get("catalogs", {}) if batch else {}
        return {
            "fields": [{"sourceHeader": header, "key": camel_case(key)} for header, key in SOURCE_FIELDS.items()],
            "catalogs": {key: catalogs.get(key, []) for key in ("countries", "impact_areas", "methods", "victim_types")},
            "sourceName": batch.source_name if batch else None,
            "importBatchId": batch.id if batch else None,
        }

    @app.get("/api/admin/candidates/{candidate_id}", dependencies=[Depends(require_admin)])
    def get_candidate(candidate_id: str, session=Depends(session_dependency)):
        candidate = session.get(Candidate, candidate_id)
        if candidate is None:
            raise HTTPException(404, "Candidate not found.")
        return candidate_dict(candidate)

    @app.post(
        "/api/admin/candidates/{candidate_id}/relationship",
        dependencies=[Depends(require_admin)],
    )
    def resolve_candidate_relationship(
        candidate_id: str,
        payload: dict = Body(...),
        session=Depends(session_dependency),
    ):
        if payload.get("decision") != "new_incident":
            raise HTTPException(
                422, "The supported decision is new_incident."
            )

        reason = payload.get("reason")
        if (
            not isinstance(reason, str)
            or not reason.strip()
            or len(reason) > 2000
        ):
            raise HTTPException(
                422, "Provide a review reason of 1–2000 characters."
            )

        with session.begin():
            candidate = session.scalar(
                select(Candidate)
                .where(Candidate.id == candidate_id)
                .with_for_update()
            )
            if candidate is None:
                raise HTTPException(404, "Candidate not found.")
            if candidate.status != "pending":
                raise HTTPException(
                    409, "Only pending candidates can be reclassified."
                )

            evidence = dict(candidate.evidence or {})
            history = list(evidence.get("relationshipReviews", []))
            history.append({
                "previousType": candidate.relationship_type,
                "previousRelatedIncidentId": candidate.related_incident_id,
                "previousReason": candidate.relationship_reason,
                "decision": "new_incident",
                "reason": reason.strip(),
            })
            evidence["relationshipReviews"] = history

            candidate.evidence = evidence
            candidate.relationship_type = "new_incident"
            candidate.related_incident_id = None
            candidate.relationship_reason = reason.strip()

            result = candidate_dict(candidate)

        return result

    @app.post(
        "/api/admin/candidates/{candidate_id}/apply-update",
        dependencies=[Depends(require_admin)],
    )
    def apply_candidate_update(
        candidate_id: str,
        payload: dict = Body(...),
        session=Depends(session_dependency),
    ):
        """Apply explicitly reviewed corrections to an existing incident.

        Never call the new-incident upsert path: that could create a duplicate
        and trigger publication. Preserve source and review history instead.
        """
        if not isinstance(payload, dict):
            raise HTTPException(422, "Expected an update review object.")
        reason = payload.get("reason")
        if not isinstance(reason, str) or not 1 <= len(reason.strip()) <= 2000:
            raise HTTPException(422, "Provide a review reason of 1–2000 characters.")
        changes = payload.get("changes", {})
        # Phase A only covers additions/corrections to these text fields.
        # Identifiers, publication status, dates and coordinates must never be
        # copied blindly from an AI-generated candidate.
        permitted_fields = {
            "description", "summary", "impact", "impact_short", "method",
            "affected_system", "countermeasures", "additional_info",
            "threat_actor", "victim_type", "impact_area",
        }
        if not isinstance(changes, dict):
            raise HTTPException(422, "changes must be an object.")
        updates = {}
        for supplied_key, value in changes.items():
            key = snake_case(supplied_key)
            if key not in permitted_fields or key in updates:
                raise HTTPException(422, f"Field {supplied_key} cannot be updated here.")
            if not isinstance(value, str) or not value.strip():
                raise HTTPException(422, "Update fields must contain verified, non-empty text.")
            updates[key] = value.strip()

        extra_urls = payload.get("sourceUrls", [])
        if not isinstance(extra_urls, list) or len(extra_urls) > 20:
            raise HTTPException(422, "sourceUrls must be an array of up to 20 URLs.")
        if not updates and not extra_urls:
            raise HTTPException(422, "Provide at least one verified change or source URL.")
        # Normalize URLs before any database write, rejecting malformed input.
        if any(not isinstance(url, str) for url in extra_urls):
            raise HTTPException(422, "sourceUrls must contain URL strings.")
        normalized_urls = [safe_url(url) for url in extra_urls]
        if any(url is None for url in normalized_urls):
            raise HTTPException(422, "sourceUrls cannot contain empty URLs.")

        with session.begin():
            candidate = session.scalar(
                select(Candidate).where(Candidate.id == candidate_id).with_for_update()
            )
            if candidate is None:
                raise HTTPException(404, "Candidate not found.")
            if candidate.status != "pending":
                raise HTTPException(409, "Only pending candidates can be applied as updates.")
            if candidate.relationship_type != "possible_update" or not candidate.related_incident_id:
                raise HTTPException(409, "Only candidates linked as possible updates can be applied.")

            incident = session.scalar(
                select(Incident)
                .where(Incident.id == candidate.related_incident_id)
                .with_for_update()
            )
            if incident is None or incident.status != "published":
                raise HTTPException(409, "The linked published incident is unavailable.")

            # Only modified fields are written, so verified old values survive.
            actual_updates = {
                key: value for key, value in updates.items()
                if getattr(incident, key) != value
            }
            candidate_url = safe_url(candidate.source_url)
            existing_urls = {source.url.rstrip("/") for source in incident.sources}
            if incident.source_url:
                existing_urls.add(safe_url(incident.source_url).rstrip("/"))
            additions = []
            for url in [candidate_url, *normalized_urls]:
                if url and url.rstrip("/") not in existing_urls:
                    existing_urls.add(url.rstrip("/"))
                    additions.append(url)
            if not actual_updates and not additions:
                raise HTTPException(422, "No new information to apply to the incident.")

            before = {key: getattr(incident, key) for key in actual_updates}
            if actual_updates:
                update_incident(session, incident, actual_updates)
            # Older imports may store the primary source on Incident only.
            # Keep it visible in the related source list when applying an update.
            if incident.source_url and all(
                source.url.rstrip("/") != safe_url(incident.source_url).rstrip("/")
                for source in incident.sources
            ):
                incident.sources.append(IncidentSource(url=incident.source_url))
            for url in additions:
                incident.sources.append(IncidentSource(url=url))
            incident.updated_at = utcnow()

            review = {
                "candidateId": candidate.id,
                "relatedIncidentId": incident.id,
                "reviewedAt": iso(utcnow()),
                "reason": reason.strip(),
                "changes": {
                    key: {"before": before[key], "after": value}
                    for key, value in actual_updates.items()
                },
                "sourceUrlsAdded": additions,
            }
            raw_fields = dict(incident.raw_fields or {})
            reviews = list(raw_fields.get("_updateReviews", []))
            reviews.append(review)
            raw_fields["_updateReviews"] = reviews
            incident.raw_fields = raw_fields

            evidence = dict(candidate.evidence or {})
            relationship_reviews = list(evidence.get("relationshipReviews", []))
            relationship_reviews.append({
                "previousType": candidate.relationship_type,
                "previousRelatedIncidentId": candidate.related_incident_id,
                "previousReason": candidate.relationship_reason,
                "decision": "applied_update",
                "reason": reason.strip(),
            })
            evidence["relationshipReviews"] = relationship_reviews
            candidate.evidence = evidence

            # Conditional transition protects against duplicate review writes.
            changed = session.execute(update(Candidate).where(
                Candidate.id == candidate_id, Candidate.status == "pending"
            ).values(status="approved", incident_id=incident.id)
                .execution_options(synchronize_session=False))
            if changed.rowcount != 1:
                raise HTTPException(409, "This candidate has already been reviewed. Refresh the queue.")
            candidate.status = "approved"
            candidate.incident_id = incident.id
            session.flush()
            result = {
                "candidate": candidate_dict(candidate),
                "incident": incident_dict(incident, details=True),
            }
        return result

    @app.post("/api/admin/candidates/{candidate_id}/approve", dependencies=[Depends(require_admin)])
    def approve_candidate(candidate_id: str, payload: dict = Body(...), session=Depends(session_dependency)):
        with session.begin():
            candidate = session.scalar(select(Candidate).where(Candidate.id == candidate_id).with_for_update())
            if candidate is None:
                raise HTTPException(404, "Candidate not found.")
            if candidate.status == "approved" and candidate.incident_id:
                return incident_dict(session.get(Incident, candidate.incident_id), details=True)
            if candidate.status != "pending":
                raise HTTPException(409, "Only pending candidates can be approved.")
            if (
                candidate.relationship_type not in {
                    "unclassified", "new_incident"
                }
                or candidate.related_incident_id is not None
            ):
                raise HTTPException(
                    409,
                    "This candidate may concern an existing incident. "
                    "Ordinary publication is blocked until its "
                    "relationship has been reviewed.",
                )
            if not isinstance(payload.get("title"), str) or not payload["title"].strip():
                raise ValueError("A human-verified title is required for approval")
            # The row lock is used by PostgreSQL. The conditional transition also
            # protects against stale requests where SELECT FOR UPDATE is absent.
            changed = session.execute(update(Candidate).where(
                Candidate.id == candidate_id, Candidate.status == "pending"
            ).values(status="approved").execution_options(synchronize_session=False))
            if changed.rowcount != 1:
                raise HTTPException(409, "This candidate has already been reviewed. Refresh the queue.")
            approved = dict(payload)
            approved["status"] = "published"
            approved.setdefault("sourceUrl", candidate.source_url)
            approved.setdefault("description", candidate.summary)
            if getattr(candidate, "origin", "rss") == "ai":
                raw_fields = approved.get("rawFields", approved.get("raw_fields", {}))
                if not isinstance(raw_fields, dict):
                    raise ValueError("raw_fields must be an object")
                approved.pop("raw_fields", None)
                approved["rawFields"] = {**raw_fields, "_aiReview": {
                    "candidateId": candidate.id,
                    "runId": getattr(candidate, "ai_run_id", None),
                    "verificationStatus": getattr(candidate, "verification_status", "unreviewed"),
                    "evidence": getattr(candidate, "evidence", None) or {},
                    "proposedFields": getattr(candidate, "proposed_fields", None) or {},
                }}
            incident, _ = upsert_incident(session, approved, notify=True, reviewed_candidate_id=candidate.id)
            candidate.status, candidate.incident_id = "approved", incident.id
            result = incident_dict(incident, details=True)
        return result

    @app.post(
        "/api/admin/candidates/{candidate_id}/reject",
        dependencies=[Depends(require_admin)]
    )
    def reject_candidate(
        candidate_id: str,
        payload: dict = Body(...),
        session=Depends(session_dependency)
    ):
        allowed_reasons = {
            "not_maritime",
            "not_cyber",
            "duplicate",
            "insufficient_evidence",
            "incorrect_information",
            "other",
        }

        reason = payload.get("reason")
        notes = payload.get("notes", "")

        if not isinstance(reason, str) or reason not in allowed_reasons:
            raise HTTPException(
                422,
                "A valid rejection reason is required."
            )

        if not isinstance(notes, str) or len(notes) > 2000:
            raise HTTPException(
                422,
                "Notes must be text of at most 2000 characters."
            )

        with session.begin():
            candidate = session.scalar(
                select(Candidate)
                .where(Candidate.id == candidate_id)
                .with_for_update()
            )

            if candidate is None:
                raise HTTPException(
                    404, "Candidate not found."
                )

            if candidate.status != "pending":
                raise HTTPException(
                    409,
                    "Only pending candidates can be rejected."
                )

            changed = session.execute(update(Candidate).where(
                Candidate.id == candidate_id, Candidate.status == "pending"
            ).values(status="rejected", rejection_reason=reason, rejection_notes=notes.strip())
                .execution_options(synchronize_session=False))
            if changed.rowcount != 1:
                raise HTTPException(409, "This candidate has already been reviewed. Refresh the queue.")
            candidate.status = "rejected"
            candidate.rejection_reason = reason
            candidate.rejection_notes = notes.strip()

        return {
            "message": "Candidate rejected successfully",
            "candidate_id": candidate_id,
            "status": "rejected",
            "reason": reason,
            "notes": notes.strip()
        }

    @app.get("/api/admin/telegram", dependencies=[Depends(require_admin)])
    def telegram_status(session=Depends(session_dependency)):
        from .models import CandidateDelivery
        return {"configured": bool(settings.telegram_bot_token),
                "testChatConfigured": bool(settings.telegram_chat_id),
                "activeSubscribers": session.scalar(select(func.count()).select_from(Subscriber).where(Subscriber.active.is_(True))),
                "pendingDeliveries": session.scalar(select(func.count()).select_from(Delivery).where(Delivery.status.in_(["pending", "retry", "sending"])))
                    + session.scalar(select(func.count()).select_from(CandidateDelivery).where(CandidateDelivery.status.in_(["pending", "retry", "sending"])))}

    @app.post("/api/admin/telegram/test", dependencies=[Depends(require_admin)])
    def test_telegram_connection():
        from uuid import uuid4
        from .workers import TelegramClient, claim_lease
        if not settings.telegram_bot_token or not settings.telegram_chat_id:
            raise HTTPException(409, "Set the Telegram bot token and test chat ID on the server first.")
        if not claim_lease(factory, "telegram_test_cooldown", str(uuid4()), seconds=60):
            raise HTTPException(429, "Wait one minute before sending another test notification.")
        result = TelegramClient(settings.telegram_bot_token).send(settings.telegram_chat_id,
            "MCAD test notification: Telegram is connected. This test did not run an AI search or publish an incident.")
        if not result.ok:
            raise HTTPException(502, "Telegram did not accept the test. Check the configured chat and bot access.")
        return {"ok": True, "messageId": result.message_id}

    @app.get("/api/admin/ai-monitor", dependencies=[Depends(require_admin)])
    def ai_monitor_status():
        from .ai_monitor import get_monitor_status
        return get_monitor_status(factory, settings)

    @app.patch("/api/admin/ai-monitor", dependencies=[Depends(require_admin)])
    def configure_ai_monitor(payload: dict = Body(...)):
        from .ai_monitor import AIUnavailable, update_monitor_settings
        try:
            return update_monitor_settings(factory, settings, payload)
        except AIUnavailable as error:
            raise HTTPException(409, str(error)) from None

    @app.post("/api/admin/ai-monitor/runs", dependencies=[Depends(require_admin)])
    def request_ai_run():
        from .ai_monitor import AIUnavailable, run_monitor_once
        try:
            # A sync FastAPI endpoint runs in its thread pool. Merely reading or
            # saving settings never calls this entry point or the Responses API.
            return run_monitor_once(factory, settings, trigger="manual")
        except AIUnavailable as error:
            raise HTTPException(409, str(error)) from None

    @app.post("/api/admin/import-workbook", dependencies=[Depends(require_admin)])
    async def import_workbook_file(request: Request):
        content_type = request.headers.get("content-type", "").split(";", 1)[0].strip().lower()
        if content_type not in {"application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", "application/octet-stream"}:
            raise HTTPException(415, "Upload the raw XLSX file with its spreadsheet content type.")
        maximum = 10 * 1024 * 1024
        content_length = request.headers.get("content-length")
        if content_length:
            try:
                if int(content_length) > maximum:
                    raise HTTPException(413, "The workbook must be at most 10 MiB.")
            except ValueError:
                raise HTTPException(400, "Invalid Content-Length header.") from None
        with TemporaryDirectory(prefix="mcad-import-") as directory:
            workbook = Path(directory) / "MCAD.xlsx"
            size = 0
            with workbook.open("wb") as handle:
                async for chunk in request.stream():
                    size += len(chunk)
                    if size > maximum:
                        raise HTTPException(413, "The workbook must be at most 10 MiB.")
                    handle.write(chunk)
            if size == 0:
                raise HTTPException(400, "The uploaded workbook is empty.")
            from .import_workbook import import_workbook
            try:
                summary = await run_in_threadpool(import_workbook, workbook, session_factory=factory)
            except (ValueError, OSError, KeyError, BadZipFile, LargeZipFile, ParseError, InvalidFileException):
                raise HTTPException(422, "The workbook could not be imported. Check its Database and Lists sheets and source columns.") from None
        fields = ["source_rows", "accepted_rows", "quarantined_rows", "mapped_rows", "unmapped_rows", "created_records", "existing_records", "reused_batch", "notification_events_created"]
        from .service import camel_case
        return {camel_case(field): summary.get(field) for field in fields}

    @app.get("/{path:path}", include_in_schema=False)
    def frontend(path: str):
        if path == "api" or path.startswith("api/"):
            raise HTTPException(404, "API route not found.")
        static_root = settings.static_dir.resolve()
        target = (static_root / path).resolve()
        if not target.is_relative_to(static_root):
            raise HTTPException(404, "File not found.")
        if target.is_file():
            return FileResponse(target)
        index = static_root / "index.html"
        if index.is_file() and (not path or "." not in path.rsplit("/", 1)[-1]):
            return FileResponse(index, headers={"Cache-Control": "no-cache"})
        raise HTTPException(404, "Frontend build not found.")

    return app


app = create_app()
