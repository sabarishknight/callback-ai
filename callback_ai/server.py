"""The web server: the enquiry form + the webhook Vapi calls during each call.

Run locally:   uvicorn callback_ai.server:app --reload --port 8000
Form:          http://localhost:8000
Expose it with ngrok so Vapi can reach the webhook:   ngrok http 8000
"""

import hmac
import json
import logging
from datetime import datetime, timedelta, timezone
from pathlib import Path

from fastapi import BackgroundTasks, Body, FastAPI, Form, Header, HTTPException, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates

from .business import load_business
from .config import get_settings
from .dialer import place_call
from .leads import new_enquiry_id, now_iso
from .phone import check_call_allowed, mask_phone, normalize_phone
from .prompts import LANGUAGE_LINES
from .scheduling import free_slots, is_bookable, slot_label
from .storage import MEETINGS, get_store

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("callback_ai")

app = FastAPI(title="CallBack AI")
templates = Jinja2Templates(directory=Path(__file__).parent / "templates")

MAX_TRANSCRIPT_CHARS = 5000  # keep sheet cells readable


@app.get("/health")
def health():
    """Quick check that the server is up: open /health in a browser."""
    settings = get_settings()
    return {"status": "ok", "storage": settings.storage_backend,
            "test_mode": settings.test_mode}


# =====================================================================
# 1. The enquiry form
# =====================================================================

def _render_form(request: Request, status_code: int = 200, **context):
    settings = get_settings()
    business = load_business(settings.business_file)
    return templates.TemplateResponse(request, "enquiry_form.html", {
        "business": business,
        "assistant": settings.assistant_name,
        "test_mode": settings.test_mode,
        "languages": {k: v["label"] for k, v in LANGUAGE_LINES.items()},
        "values": {},
        **context,
    }, status_code=status_code)


@app.get("/", response_class=HTMLResponse)
def enquiry_form(request: Request):
    return _render_form(request)


# Plain `def` (not `async def`): FastAPI runs it in a thread, so slow Google
# Sheets calls don't block other requests.
@app.post("/enquiry", response_class=HTMLResponse)
def submit_enquiry(
    request: Request,
    background_tasks: BackgroundTasks,
    name: str = Form(""),
    phone: str = Form(""),
    company: str = Form(""),
    service: str = Form(""),
    message: str = Form(""),
    language: str = Form("english"),
    consent: str = Form(""),
):
    settings = get_settings()
    business = load_business(settings.business_file)
    values = {"name": name, "phone": phone, "company": company, "service": service,
              "message": message, "language": language}

    # --- 1. Validate ---
    error = None
    try:
        number = normalize_phone(phone)
    except ValueError:
        number, error = "", "Please enter a valid mobile number, e.g. 98765 43210."
    if not name.strip():
        error = "Please tell us your name."
    elif service not in business.services:
        error = "Please choose what you need help with."
    elif language not in LANGUAGE_LINES:
        error = "Please choose a language."
    elif not consent:
        error = "Please tick the box to agree to a call from our AI assistant."
    if error:
        return _render_form(request, 400, error=error, values=values)

    store = get_store(settings)

    # --- 2. Same number just submitted? Don't call twice. ---
    cutoff = datetime.now(timezone.utc) - timedelta(minutes=settings.duplicate_window_minutes)
    for row in store.read_all():
        if (row.get("phone") == number
                and row.get("status") in ("call_scheduled", "calling", "call_ended")
                and datetime.fromisoformat(row["submitted_at"]) > cutoff):
            status = "duplicate"
            break
    else:
        # --- 3. Test mode: only approved numbers get a call. ---
        allowed, _ = check_call_allowed(number, settings)
        status = "call_scheduled" if allowed else "blocked_test_mode"

    # --- 4. Save the lead (always, even if we won't call) ---
    enquiry_id = new_enquiry_id()
    store.upsert(enquiry_id, {
        "submitted_at": now_iso(),
        "name": name.strip()[:80],
        "phone": number,
        "company": company.strip()[:120],
        "service_requested": service,
        "message": message.strip()[:1000],
        "language": language,
        "status": status,
    })
    log.info("Enquiry %s from %s -> %s", enquiry_id, mask_phone(number), status)

    # --- 5. Call in the background, after a short delay ---
    if status == "call_scheduled":
        background_tasks.add_task(place_call, enquiry_id, settings,
                                  settings.call_delay_seconds)

    return _render_form(request, submitted={
        "status": status,
        "first_name": name.strip().split()[0],
        "phone_masked": mask_phone(number),
        "delay": settings.call_delay_seconds,
    })


# =====================================================================
# 2. The Vapi webhook
# =====================================================================

@app.post("/vapi/webhook")
def vapi_webhook(
    payload: dict = Body(...),
    enquiry_id: str = "",
    x_vapi_secret: str | None = Header(default=None),
):
    settings = get_settings()

    # Security: if a secret is configured, the request must carry it.
    if settings.webhook_secret and not hmac.compare_digest(
        x_vapi_secret or "", settings.webhook_secret
    ):
        raise HTTPException(status_code=401, detail="bad or missing x-vapi-secret")

    message = payload.get("message") or {}
    message_type = message.get("type", "")
    call = message.get("call") or {}
    # enquiry_id comes from the URL we gave Vapi. Fall back to the call ID
    # so an unexpected call is still recorded somewhere.
    enquiry_id = enquiry_id or call.get("id") or "unknown"

    if message_type == "tool-calls":
        return {"results": [_run_tool(tool_call, enquiry_id, settings)
                            for tool_call in message.get("toolCallList") or []]}
    if message_type == "end-of-call-report":
        return _handle_end_of_call(message, enquiry_id, settings)

    # Other event types are fine to ignore, but always answer 200.
    return {"ok": True, "ignored": message_type}


def _run_tool(tool_call: dict, enquiry_id: str, settings) -> dict:
    """Do what Claude asked, and return a sentence telling Claude what happened."""
    function = tool_call.get("function") or {}
    name = function.get("name")
    args = function.get("arguments") or {}
    if isinstance(args, str):  # sometimes arrives as a JSON string
        args = json.loads(args or "{}")

    handlers = {
        "get_available_slots": _tool_get_slots,
        "book_meeting": _tool_book_meeting,
        "save_call_result": _tool_save_result,
    }
    try:
        if name not in handlers:
            raise ValueError(f"Unknown tool {name}")
        result = handlers[name](args, enquiry_id, settings)
        return {"toolCallId": tool_call.get("id"), "result": result}
    except Exception as error:
        # Never crash the live call: tell Claude so it can recover politely.
        log.exception("Tool %s failed for %s", name, enquiry_id)
        return {"toolCallId": tool_call.get("id"), "error": str(error)}


def _booked_slots(settings) -> set[str]:
    return {row["slot_id"] for row in get_store(settings, MEETINGS).read_all()}


def _offer_text(settings) -> str:
    business = load_business(settings.business_file)
    slots = free_slots(business, _booked_slots(settings), datetime.now(timezone.utc))
    if not slots:
        return "No free slots in the next few days. Offer a callback at a time they choose."
    listed = "; ".join(f"slot_id {s} = {slot_label(s)}" for s in slots)
    return (f"Free times (read out the friendly time, never the slot_id): {listed}. "
            "Offer two or three that suit them.")


def _tool_get_slots(args: dict, enquiry_id: str, settings) -> str:
    return _offer_text(settings)


def _tool_book_meeting(args: dict, enquiry_id: str, settings) -> str:
    business = load_business(settings.business_file)
    raw = str(args.get("slot_id", "")).strip()
    try:
        slot_id = datetime.fromisoformat(raw).isoformat()  # tidy small format slips
    except ValueError:
        return f"'{raw}' is not a valid slot_id. {_offer_text(settings)}"

    now = datetime.now(timezone.utc)
    store = get_store(settings)
    enquiry = store.get(enquiry_id) or {}
    if not is_bookable(business, slot_id, _booked_slots(settings), now):
        return f"Sorry, that time is no longer available. {_offer_text(settings)}"

    # insert_if_absent is the real double-booking guard (checked under a lock).
    booked = get_store(settings, MEETINGS).insert_if_absent(slot_id, {
        "slot_label": slot_label(slot_id),
        "enquiry_id": enquiry_id,
        "name": enquiry.get("name", ""),
        "company": enquiry.get("company", ""),
        "booked_at": now_iso(),
    })
    if not booked:
        return f"Sorry, someone just took that time. {_offer_text(settings)}"

    store.upsert(enquiry_id, {"meeting_slot": slot_id})
    log.info("Meeting booked for %s at %s", enquiry_id, slot_id)
    return (f"Booked for {slot_label(slot_id)}. Confirm the day and time to the caller "
            "and tell them the team will send the Google Meet link on WhatsApp.")


def _tool_save_result(args: dict, enquiry_id: str, settings) -> str:
    row = get_store(settings).upsert(enquiry_id, {
        "interest": args.get("interest", ""),
        "service_type": args.get("service_type", ""),
        "budget_range": args.get("budget_range", ""),
        "timeline": args.get("timeline", ""),
        "callback_time": args.get("callback_time", ""),
        "questions": args.get("questions", ""),
        "summary": args.get("summary", ""),
    })
    log.info("Saved %s -> %s, %s", enquiry_id, row["outcome"], row["lead_score"] or "unscored")
    return "Saved. Now say goodbye and end the call."


def _handle_end_of_call(message: dict, enquiry_id: str, settings) -> dict:
    """The call is over. Record why it ended, how long it took and the transcript."""
    artifact = message.get("artifact") or {}
    analysis = message.get("analysis") or {}
    transcript = artifact.get("transcript") or message.get("transcript") or ""
    duration = message.get("durationSeconds")

    updates = {
        "status": "call_ended",
        "call_id": (message.get("call") or {}).get("id", ""),
        "ended_reason": message.get("endedReason", ""),
        "duration_sec": int(round(float(duration))) if duration not in (None, "") else "",
        "transcript": transcript[:MAX_TRANSCRIPT_CHARS],
    }
    # Prefer the summary Claude wrote in save_call_result; use Vapi's own
    # summary only if Claude never called it (e.g. the caller hung up).
    defaults = {"summary": analysis.get("summary") or message.get("summary") or ""}

    try:
        row = get_store(settings).upsert(enquiry_id, updates, defaults)
        log.info("Call for %s ended (%s) -> %s", enquiry_id, updates["ended_reason"],
                 row["outcome"])
    except Exception:
        log.exception("Could not save end-of-call report for %s", enquiry_id)
    return {"ok": True}
