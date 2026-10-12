"""Lead scoring: hot / warm / cold, with the reason spelled out.

Deliberately simple rules in plain Python, not an AI judgement, so:
  - the same answers always give the same score, and
  - a sales manager can read exactly why a lead is hot.

Points (maximum 8):
  Intent    meeting booked +3 | interested +2 | maybe / call later +1
  Budget    above ₹1L +3 | ₹50k–1L +2 | ₹25k–50k or not sure +1 | under ₹25k +0
  Timeline  within 2 weeks or a month +2 | 1–3 months +1 | just exploring +0
Hot = 6 or more, warm = 3 to 5, cold = 2 or less.
"Not interested" is always cold.
"""

from .business import BUDGET_RANGES, TIMELINES

INTENT_POINTS = {"meeting": 3, "yes": 2, "maybe": 1, "call_later": 1}
BUDGET_POINTS = {"1l_plus": 3, "50k_1l": 2, "25k_50k": 1, "not_sure": 1, "under_25k": 0}
TIMELINE_POINTS = {"asap": 2, "this_month": 2, "1_3_months": 1, "exploring": 0}

HOT_THRESHOLD = 6
WARM_THRESHOLD = 3


def score_lead(interest: str, budget_range: str, timeline: str,
               meeting_slot: str) -> tuple[str, str]:
    """Return (score, reason). score is "hot", "warm", "cold" or "" (not reached)."""
    interest = (interest or "").strip().lower()
    if not interest:
        return "", ""  # we never got to talk to them
    if interest == "no":
        return "cold", "Said not interested."

    intent_key = "meeting" if meeting_slot else interest
    intent = INTENT_POINTS.get(intent_key, 0)
    budget = BUDGET_POINTS.get(budget_range, 0)
    timing = TIMELINE_POINTS.get(timeline, 0)
    total = intent + budget + timing

    intent_text = {"meeting": "Meeting booked", "yes": "Interested",
                   "maybe": "Maybe interested", "call_later": "Asked for a call back"}
    parts = [
        f"{intent_text.get(intent_key, 'Unclear intent')} (+{intent})",
        f"budget {BUDGET_RANGES.get(budget_range, 'not given')} (+{budget})",
        f"timeline {TIMELINES.get(timeline, 'not given').lower()} (+{timing})",
    ]

    if total >= HOT_THRESHOLD:
        score = "hot"
    elif total >= WARM_THRESHOLD:
        score = "warm"
    else:
        score = "cold"
    return score, f"{', '.join(parts)} = {total}/8."
