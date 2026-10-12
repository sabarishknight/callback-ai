"""Place the follow-up call for one enquiry.

Runs in the background after the form is submitted, so the visitor gets an
instant "thank you" page while we wait CALL_DELAY_SECONDS and then dial.
"""

import logging
import time
from datetime import datetime, timezone

from .business import load_business, load_faq
from .config import Settings
from .leads import Enquiry, now_iso
from .phone import mask_phone
from .storage import get_store
from .vapi import build_assistant, start_phone_call

log = logging.getLogger("callback_ai")


def place_call(enquiry_id: str, settings: Settings, delay_seconds: int = 0) -> None:
    if delay_seconds:
        time.sleep(delay_seconds)

    store = get_store(settings)
    row = store.get(enquiry_id)
    if not row or row.get("status") != "call_scheduled":
        return  # deleted, or already handled
    enquiry = Enquiry.from_row(row)

    try:
        # Business profile + FAQ are read fresh, so edits apply without a restart.
        business = load_business(settings.business_file)
        faq = load_faq(settings.faq_file)
        now = datetime.now(timezone.utc)
        assistant = build_assistant(business, faq, enquiry, settings, now, phone_call=True)
        call = start_phone_call(assistant, enquiry.phone, enquiry.name, settings)
    except Exception as error:
        log.exception("Call to %s failed", mask_phone(enquiry.phone))
        store.upsert(enquiry_id, {"status": "call_failed",
                                  "summary": f"Call could not be placed: {error}"[:300]})
        return

    submitted = datetime.fromisoformat(row["submitted_at"])
    store.upsert(enquiry_id, {
        "status": "calling",
        "call_id": call.get("id", ""),
        "called_at": now_iso(),
        # Speed-to-lead: how long the enquiry waited for a call.
        "seconds_to_call": int((now - submitted).total_seconds()),
    })
    log.info("Calling %s for %s (call %s)", mask_phone(enquiry.phone),
             enquiry_id, call.get("id"))
