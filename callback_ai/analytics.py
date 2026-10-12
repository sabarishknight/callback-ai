"""Turn raw enquiry rows into the numbers the dashboard shows.

Kept separate from the Streamlit UI so the logic is easy to test and explain.
"""

import pandas as pd

from .business import BUDGET_RANGES, TIMELINES, Business
from .scheduling import slot_label

# Outcomes in the order a manager reads them, with friendly names.
OUTCOME_LABELS = {
    "meeting_booked": "Meeting booked",
    "interested": "Interested, no meeting yet",
    "maybe": "Maybe",
    "call_later": "Call later",
    "not_interested": "Not interested",
    "voicemail": "Voicemail",
    "no_answer": "No answer",
    "hung_up_early": "Hung up early",
    "pending": "Call in progress",
    "not_called": "Not called",
}

# Outcomes where we actually spoke to the person.
REACHED = {"meeting_booked", "interested", "maybe", "call_later",
           "not_interested", "hung_up_early"}

SCORE_LABELS = {"hot": "Hot", "warm": "Warm", "cold": "Cold", "": "Not scored"}


def enquiries_frame(rows: list[dict], business: Business) -> pd.DataFrame:
    """Rows from the sheet -> a tidy table with readable labels added."""
    df = pd.DataFrame(rows)
    if df.empty:
        return df
    df = df.fillna("")
    df["submitted_at"] = pd.to_datetime(df["submitted_at"], utc=True, errors="coerce")
    df["submitted_ist"] = df["submitted_at"].dt.tz_convert("Asia/Kolkata")
    df["seconds_to_call"] = pd.to_numeric(df["seconds_to_call"], errors="coerce")
    df["duration_sec"] = pd.to_numeric(df["duration_sec"], errors="coerce")

    df["outcome_label"] = df["outcome"].map(OUTCOME_LABELS).fillna(df["outcome"])
    df["score_label"] = df["lead_score"].map(SCORE_LABELS).fillna("Not scored")
    # Prefer what the agent confirmed on the call over what the form said.
    service = df["service_type"].where(df["service_type"] != "", df["service_requested"])
    df["service_label"] = service.map(business.service_label)
    df["budget_label"] = df["budget_range"].map(BUDGET_RANGES).fillna("")
    df["timeline_label"] = df["timeline"].map(TIMELINES).fillna("")
    df["meeting_label"] = df["meeting_slot"].map(lambda s: slot_label(s) if s else "")
    return df.sort_values("submitted_at", ascending=False).reset_index(drop=True)


def headline_numbers(df: pd.DataFrame) -> dict:
    if df.empty:
        return {"total": 0, "reached": 0, "reach_rate": 0.0, "meetings": 0,
                "hot": 0, "median_seconds_to_call": None}
    reached = int(df["outcome"].isin(REACHED).sum())
    called = int(df["status"].isin(["calling", "call_ended"]).sum())
    median = df["seconds_to_call"].median()
    return {
        "total": len(df),
        "reached": reached,
        "reach_rate": reached / called if called else 0.0,
        "meetings": int((df["outcome"] == "meeting_booked").sum()),
        "hot": int((df["lead_score"] == "hot").sum()),
        "median_seconds_to_call": None if pd.isna(median) else float(median),
    }


def outcome_counts(df: pd.DataFrame) -> pd.DataFrame:
    counts = df["outcome"].value_counts() if not df.empty else pd.Series(dtype=int)
    rows = [{"outcome": label, "enquiries": int(counts[key])}
            for key, label in OUTCOME_LABELS.items() if key in counts]
    return pd.DataFrame(rows, columns=["outcome", "enquiries"])


def score_counts(df: pd.DataFrame) -> pd.DataFrame:
    """Hot / warm / cold, only for leads we actually spoke to."""
    scored = df[df["lead_score"] != ""] if not df.empty else df
    counts = scored["lead_score"].value_counts() if not scored.empty else pd.Series(dtype=int)
    return pd.DataFrame(
        [{"score": SCORE_LABELS[k], "leads": int(counts.get(k, 0))} for k in ("hot", "warm", "cold")]
    )


def enquiries_per_day(df: pd.DataFrame) -> pd.DataFrame:
    if df.empty:
        return pd.DataFrame(columns=["day", "enquiries"])
    days = df["submitted_ist"].dt.date.value_counts().sort_index()
    return pd.DataFrame({"day": pd.to_datetime(days.index), "enquiries": days.values})
