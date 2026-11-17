import json
from dataclasses import replace
from unittest.mock import Mock

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from mcad.api import create_app
from mcad.candidate_alerts import create_candidate_notification, deliver_candidate_one
from mcad.config import Settings
from mcad.db import get_session_factory, make_engine
from mcad.migrations import migrate
from mcad.models import Candidate, Subscriber
from mcad.prompt_learning import active_prompt, propose_editorial_draft_once
from mcad.service import upsert_incident
from mcad.telegram_feedback import TelegramFeedback
from mcad.workers import TelegramClient, deliver_one, process_update


ADMIN = {"Authorization": "Bearer test-admin"}
LEARNING = "/api/admin/learning"
ARTICLE = "https://example.org/fictional-port-report"


@pytest.fixture
def isolated_db(tmp_path):
    engine = make_engine(f"sqlite:///{tmp_path / 'learning.sqlite3'}")
    migrate(engine)
    try:
        yield engine, get_session_factory(engine)
    finally:
        engine.dispose()


def settings(engine, enabled=False):
    return Settings(database_url=str(engine.url), admin_api_token="test-admin",
        enable_workers=False, enable_ai_search=False, enable_ai_feedback=enabled, openai_api_key="fake-key")


def callback(update_id, callback_data, *, chat=1234, actor=1234, message_id=42):
    return {"update_id": update_id, "callback_query": {"id": f"query-{update_id}", "data": callback_data,
        "from": {"id": actor}, "message": {"chat": {"type": "private", "id": chat}, "message_id": message_id}}}


def comment(update_id, actor=1234):
    return {"update_id": update_id, "message": {"chat": {"type": "private", "id": actor},
        "from": {"id": actor}, "text": "/feedback This report refers to the earlier incident.",
        "reply_to_message": {"message_id": 42}}}


@pytest.mark.parametrize("candidate_alert", [True, False])
def test_feedback_on_alert_keeps_url_and_verifies_recipient_and_message(isolated_db, monkeypatch, candidate_alert):
    _, factory = isolated_db
    with factory.begin() as session:
        session.add(Subscriber(chat_id="1234"))
        session.flush()
        if candidate_alert:
            candidate = Candidate(title="Fictional port report", source_url=ARTICLE, origin="ai", verification_status="supported")
            session.add(candidate)
            create_candidate_notification(session, candidate)
        else:
            upsert_incident(session, {"title": "Fictional port report", "sourceUrl": ARTICLE}, notify=True)
    http = Mock()
    http.return_value.json.return_value = {"ok": True, "result": {"message_id": 42}}
    monkeypatch.setattr("mcad.workers.requests.post", http)
    bot = TelegramClient("test-bot-token")
    send_one = deliver_candidate_one if candidate_alert else deliver_one
    assert send_one(factory, bot)
    request = http.call_args.kwargs["json"]
    assert request["text"] == ARTICLE
    assert request["chat_id"] == "1234"
    buttons = {btn["text"]: btn["callback_data"] for row in request["reply_markup"]["inline_keyboard"] for btn in row}
    assert set(buttons) == {"Relevant", "Duplicate", "Old news", "Not maritime-related", "Incorrect information"}
    assert max(len(data.encode()) for data in buttons.values()) <= 64

    click = buttons["Duplicate"]
    assert "unavailable" in process_update(factory, callback(1, click, actor=4321)).text
    assert "unavailable" in process_update(factory, callback(2, click, message_id=43)).text
    assert "unavailable" in process_update(factory, callback(3, click, actor=4321, chat=4321)).text
    with factory() as session:
        assert session.scalar(select(TelegramFeedback)) is None
    answer = process_update(factory, callback(4, click))
    assert "Feedback saved" in answer.text
    bot.answer_callback(answer.query_id, answer.text)
    assert http.call_args.args[0].endswith("/answerCallbackQuery")
    assert http.call_args.kwargs["json"]["callback_query_id"] == "query-4"
    assert process_update(factory, callback(4, click)) is None
    assert process_update(factory, comment(5, actor=4321)) is None
    assert process_update(factory, comment(6))[1] == "Comment saved."
    process_update(factory, callback(7, click))  # Re-rating preserves the same reason's comment.
    with factory() as session:
        [feedback] = session.scalars(select(TelegramFeedback)).all()
        assert feedback.reason == "duplicate"
        assert feedback.comment == "This report refers to the earlier incident."
        assert feedback.source_url == ARTICLE


def model_json(result):
    return {"status": "completed", "output": [{"type": "message", "content": [{"type": "output_text", "text": json.dumps(result)}]}]}


def test_editorial_rejection_is_used_for_an_automatic_draft_once(isolated_db, monkeypatch):
    engine, factory = isolated_db
    provider = Mock()
    client_class = Mock(return_value=provider)
    monkeypatch.setattr("mcad.ai_monitor.OpenAIResponsesClient", client_class)
    with factory.begin() as session:
        candidate = Candidate(title="Fictional port outage", summary="The proposal called it ransomware.", source_url=ARTICLE)
        session.add(candidate)
        session.flush()
        candidate_id = candidate.id

    explanation = "The sources confirmed a service outage but did not confirm ransomware."
    with TestClient(create_app(settings(engine), engine)) as browser:
        rejection = browser.post(f"/api/admin/candidates/{candidate_id}/reject", headers=ADMIN,
            json={"reason": "other", "notes": explanation})
        assert rejection.status_code == 200
        entry, = browser.get(LEARNING, headers=ADMIN).json()["feedback"]
        assert entry["editorialDecision"] is True
        assert entry["reviewNotes"] == explanation
        assert entry["status"] == "confirmed"

    assert propose_editorial_draft_once(factory, settings(engine)) is None
    client_class.assert_not_called()

    def propose(payload):
        inputs = json.loads(payload["input"][1]["content"])
        if payload["text"]["format"]["name"] == "prompt_regression":
            return model_json({"results": [{"caseId": inputs["cases"][0]["caseId"],
                "verdict": "incorrect_information", "rationale": "Sources confirmed an outage only."}]})
        example, = inputs["examples"]
        assert example["evidence"] == "The proposal called it ransomware."
        assert example["moderatorFacts"] == explanation
        assert example["confirmedDecision"] == "other"
        assert "tools" not in payload
        return model_json({"summary": "Require attribution for ransomware claims",
            "discoveryInstructions": "Distinguish outages from reported ransomware.",
            "verificationInstructions": "Require independent sources for ransomware attribution."})

    provider.create_response.side_effect = propose
    enabled = settings(engine, enabled=True)
    draft = propose_editorial_draft_once(factory, enabled)
    assert draft["createdByAI"] and draft["pendingCases"] == 1
    assert propose_editorial_draft_once(factory, enabled) is None
    provider.create_response.assert_called_once()
    with factory() as session:
        case = session.get(TelegramFeedback, entry["id"])
        assert case.editorial_drafted_at is not None
        assert active_prompt(session)["versionId"] is None
    with TestClient(create_app(enabled, engine)) as browser:
        evaluate = browser.post(f"{LEARNING}/versions/{draft['id']}/evaluate", headers=ADMIN)
        assert evaluate.json()["passedCases"] == 1


def test_confirmed_cases_gate_activation_and_rollback_without_starting_search(isolated_db, monkeypatch):
    engine, factory = isolated_db
    fake_provider = Mock()
    client_class = Mock(return_value=fake_provider)
    monkeypatch.setattr("mcad.ai_monitor.OpenAIResponsesClient", client_class)
    with factory.begin() as session:
        report = TelegramFeedback(delivery_kind="c", delivery_id="synthetic-delivery", actor_id="1234",
            title="Fictional duplicate report", evidence="Two articles cover the same event.",
            reason="duplicate", comment="Private subscriber comment")
        session.add(report)
        session.flush()
        feedback_id = report.id

    with TestClient(create_app(settings(engine), engine)) as browser:
        assert browser.get(LEARNING).status_code == 401
        confirm = f"{LEARNING}/feedback/{feedback_id}/review"
        assert browser.post(confirm, headers=ADMIN, json={"status": "confirmed"}).status_code == 422
        result = browser.post(confirm, headers=ADMIN, json={"status": "confirmed", "notes": "Both reports cover the same named ship and date."})
        assert result.status_code == 200
        assert browser.post(f"{LEARNING}/versions/propose", headers=ADMIN).status_code == 409
        state = browser.get(LEARNING, headers=ADMIN).json()
        assert state["regressionCaseCount"] == 1
        assert "actorId" not in state["feedback"][0]
        client_class.assert_not_called()

    def response(payload):
        inputs = json.loads(payload["input"][1]["content"])
        assert payload["store"] is False
        assert "tools" not in payload
        assert "Private subscriber comment" not in json.dumps(inputs)
        if payload["text"]["format"]["name"] == "prompt_improvement":
            assert inputs["caseCounts"] == {"duplicate": 1}
            return model_json({"summary": "Compare event dates before including a lead",
                "discoveryInstructions": "Compare the named ship and date before including a lead.",
                "verificationInstructions": "Check prior reports for the same ship and event date."})
        assert "confirmedDecision" not in inputs["cases"][0]
        assert inputs["cases"][0]["caseId"] == feedback_id
        return model_json({"results": [{"caseId": feedback_id, "verdict": evaluation_verdict,
            "rationale": "The evidence names the same ship and event date."}]})

    fake_provider.create_response.side_effect = response
    with TestClient(create_app(settings(engine, enabled=True), engine)) as browser:
        propose = lambda: browser.post(f"{LEARNING}/versions/propose", headers=ADMIN)
        first = propose()
        assert first.status_code == 201, first.text
        failed_id = first.json()["id"]
        evaluate = lambda version: browser.post(f"{LEARNING}/versions/{version}/evaluate", headers=ADMIN)
        activate = lambda version: browser.post(f"{LEARNING}/versions/{version}/activate", headers=ADMIN)
        assert activate(failed_id).status_code == 409
        evaluation_verdict = "relevant"
        evaluation = evaluate(failed_id)
        assert evaluation.status_code == 200, evaluation.text
        assert evaluation.json()["failedCases"] == 1
        assert activate(failed_id).status_code == 409
        assert browser.post(f"{LEARNING}/rollback", headers=ADMIN, json={"versionId": failed_id}).status_code == 409

        next_version = propose().json()["id"]
        evaluation_verdict = "duplicate"
        assert evaluate(next_version).json()["passedCases"] == 1
        calls = fake_provider.create_response.call_count
        assert evaluate(next_version).status_code == 409
        assert fake_provider.create_response.call_count == calls
        assert activate(next_version).json() == {"activeVersionId": next_version}
        results = browser.get(f"{LEARNING}/versions/{next_version}/results", headers=ADMIN).json()
        assert results["items"][0]["expected"] == results["items"][0]["actual"] == "duplicate"
        with factory() as session:
            assert active_prompt(session)["versionId"] == next_version
        assert browser.post(f"{LEARNING}/rollback", headers=ADMIN, json={"versionId": None}).json() == {"activeVersionId": None}
        with factory() as session:
            assert active_prompt(session)["discoveryInstructions"] == ""

    with TestClient(create_app(replace(settings(engine, enabled=True), ai_verification_model="new-model"), engine)) as browser:
        assert browser.post(f"{LEARNING}/versions/{next_version}/activate", headers=ADMIN).status_code == 409
