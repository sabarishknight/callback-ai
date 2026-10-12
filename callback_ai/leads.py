"""What an enquiry (lead) looks like, and how its call outcome is worked out.

One enquiry = one row in the "enquiries" sheet. The row starts when the form
is submitted and gets filled in as the call happens.
"""

import secrets
from dataclasses import dataclass
from datetime import datetime, timezone

from .scoring import score_lead

# Column order in the sheet / CSV.
ENQUIRY_FIELDS = [
    # From the form
    "enquiry_id", "submitted_at", "name", "phone", "company",
    "service_requested", "message", "language",
    # Call pipeline
    "status", "call_id", "called_at", "seconds_to_call",
    # What the agent learned (save_call_result / book_meeting tools)
    "interest", "service_type", "budget_range", "timeline", "meeting_slot",
    "callback_time", "questions", "summary",
    # Worked out by us
    "outcome", "lead_score", "score_reason",
    # End-of-call report
    "ended_reason", "duration_sec", "transcript", "updated_at",
]

MEETING_FIELDS = ["slot_id", "slot_label", "enquiry_id", "name", "company", "booked_at"]

# status = where the enquiry is in the pipeline (set by our code)
#   call_scheduled -> calling -> call_ended
#   blocked_test_mode | duplicate | call_failed  (never called)

# Words in Vapi's endedReason that mean we never spoke to the person.
NO_ANSWER_HINTS = ("did-not-answer", "no-answer", "busy", "failed",
                   "unanswered", "invalid", "not-found")

INTEREST_TO_OUTCOME = {
    "yes": "interested",
    "maybe": "maybe",
    "no": "not_interested",
    "call_later": "call_later",
}


@dataclass
class Enquiry:
    enquiry_id: str
    name: str
    phone: str
    company: str
    service_requested: str
    message: str
    language: str  # "english" or "hindi"

    @property
    def first_name(self) -> str:
        return self.name.split()[0] if self.name else "there"

    @classmethod
    def from_row(cls, row: dict) -> "Enquiry":
        return cls(**{f: str(row.get(f, "")) for f in cls.__dataclass_fields__})


def new_enquiry_id() -> str:
    """e.g. ENQ-20261012-1430-a3f9: readable, sortable, and unique enough."""
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M")
    return f"ENQ-{stamp}-{secrets.token_hex(2)}"


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def classify_outcome(row: dict) -> str:
    """One simple label for the dashboard, from everything we know so far."""
    status = str(row.get("status", ""))
    interest = str(row.get("interest", "")).strip().lower()
    reason = str(row.get("ended_reason", "")).strip().lower()

    if status in ("blocked_test_mode", "duplicate", "call_failed"):
        return "not_called"
    if row.get("meeting_slot"):
        return "meeting_booked"
    if interest in INTEREST_TO_OUTCOME:
        return INTEREST_TO_OUTCOME[interest]
    if "voicemail" in reason:
        return "voicemail"
    if any(hint in reason for hint in NO_ANSWER_HINTS):
        return "no_answer"
    if reason:
        return "hung_up_early"  # they picked up, but nothing was saved
    return "pending"  # call not finished yet


def derive_fields(row: dict) -> dict:
    """Recalculate outcome + lead score every time the row changes."""
    row["outcome"] = classify_outcome(row)
    row["lead_score"], row["score_reason"] = score_lead(
        str(row.get("interest", "")), str(row.get("budget_range", "")),
        str(row.get("timeline", "")), str(row.get("meeting_slot", "")),
    )
    return row
