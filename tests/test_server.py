"""End-to-end: form submit -> call placed -> tools -> end-of-call, with Vapi faked."""

import re

import pytest
from fastapi.testclient import TestClient

from callback_ai import dialer
from callback_ai.config import get_settings
from callback_ai.server import app
from callback_ai.storage import MEETINGS, get_store

SECRET = {"x-vapi-secret": "s3cret"}
FORM = {"name": "Priya Sharma", "phone": "98765 43210", "company": "Chai Corner",
        "service": "seo", "message": "Need more Google traffic", "language": "english",
        "consent": "yes"}


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("STORAGE_BACKEND", "csv")
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    monkeypatch.setenv("VAPI_WEBHOOK_SECRET", "s3cret")
    monkeypatch.setenv("TEST_MODE", "true")
    monkeypatch.setenv("ALLOWED_NUMBERS", "+919876543210")
    monkeypatch.setenv("CALL_DELAY_SECONDS", "0")
    monkeypatch.setenv("PUBLIC_BASE_URL", "https://example.test")

    # Fake Vapi: record the call instead of dialling.
    placed = []

    def fake_start(assistant, number, name, settings):
        placed.append((number, assistant))
        return {"id": "call-xyz"}

    monkeypatch.setattr(dialer, "start_phone_call", fake_start)
    test_client = TestClient(app)
    test_client.placed = placed
    return test_client


def enquiries():
    return get_store(get_settings()).read_all()


def tool_call(client, enquiry_id, name, arguments):
    payload = {"message": {"type": "tool-calls", "call": {"id": "call-xyz"},
                           "toolCallList": [{"id": "t1", "type": "function",
                                             "function": {"name": name, "arguments": arguments}}]}}
    response = client.post(f"/vapi/webhook?enquiry_id={enquiry_id}", json=payload, headers=SECRET)
    assert response.status_code == 200
    return response.json()["results"][0]["result"]


def test_form_page_loads(client):
    response = client.get("/")
    assert response.status_code == 200
    assert "Brightpath Digital" in response.text and 'name="consent"' in response.text


def test_form_rejects_missing_consent(client):
    response = client.post("/enquiry", data={**FORM, "consent": ""})
    assert response.status_code == 400
    assert "tick the box" in response.text
    assert enquiries() == []


def test_test_mode_saves_lead_but_does_not_call(client):
    response = client.post("/enquiry", data={**FORM, "phone": "99999 11111"})
    assert "Test mode" in response.text
    assert client.placed == []
    assert enquiries()[0]["status"] == "blocked_test_mode"


def test_duplicate_submission_is_called_once(client):
    client.post("/enquiry", data=FORM)
    response = client.post("/enquiry", data=FORM)
    assert "won't call twice" in response.text
    assert len(client.placed) == 1


def test_full_lead_lifecycle(client):
    # 1. Form submitted -> call placed in the background.
    response = client.post("/enquiry", data=FORM)
    assert "will call you" in response.text
    assert client.placed[0][0] == "+919876543210"
    row = enquiries()[0]
    enquiry_id = row["enquiry_id"]
    assert row["status"] == "calling" and row["call_id"] == "call-xyz"
    assert row["seconds_to_call"] != ""
    assert f"enquiry_id={enquiry_id}" in client.placed[0][1]["server"]["url"]

    # 2. Agent asks for free slots, then books the first one.
    offered = tool_call(client, enquiry_id, "get_available_slots", {})
    slot = re.search(r"slot_id (\S+) =", offered).group(1)
    booked = tool_call(client, enquiry_id, "book_meeting", {"slot_id": slot})
    assert booked.startswith("Booked for")

    # 3. Same slot again -> refused, never double-booked.
    again = tool_call(client, enquiry_id, "book_meeting", {"slot_id": slot})
    assert "no longer available" in again
    assert len(get_store(get_settings(), MEETINGS).read_all()) == 1

    # 4. Agent saves the qualification answers.
    tool_call(client, enquiry_id, "save_call_result", {
        "interest": "yes", "service_type": "seo", "budget_range": "50k_1l",
        "timeline": "this_month", "summary": "Wants SEO, booked a call.",
    })

    # 5. Vapi sends the end-of-call report.
    report = {"message": {"type": "end-of-call-report", "call": {"id": "call-xyz"},
                          "endedReason": "assistant-ended-call", "durationSeconds": 131.4,
                          "artifact": {"transcript": "AI: Hi Priya..."},
                          "analysis": {"summary": "Vapi's summary"}}}
    client.post(f"/vapi/webhook?enquiry_id={enquiry_id}", json=report, headers=SECRET)

    row = enquiries()[0]
    assert row["status"] == "call_ended"
    assert row["outcome"] == "meeting_booked"
    assert row["lead_score"] == "hot"                  # 3 + 2 + 2 = 7
    assert row["meeting_slot"] == slot
    assert row["summary"] == "Wants SEO, booked a call."
    assert row["duration_sec"] == "131"


def test_webhook_rejects_wrong_secret(client):
    response = client.post("/vapi/webhook", json={"message": {"type": "tool-calls"}},
                           headers={"x-vapi-secret": "nope"})
    assert response.status_code == 401


def test_unknown_tool_returns_error_not_crash(client):
    payload = {"message": {"type": "tool-calls", "toolCallList": [
        {"id": "t9", "function": {"name": "transfer_money", "arguments": {}}}]}}
    response = client.post("/vapi/webhook?enquiry_id=E1", json=payload, headers=SECRET)
    assert response.status_code == 200
    assert "Unknown tool" in response.json()["results"][0]["error"]
