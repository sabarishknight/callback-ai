"""The demo business: its profile (business.json) and FAQ (faq.md),
plus the fixed answer options the agent uses when qualifying a lead."""

import json
from dataclasses import dataclass
from pathlib import Path

# Qualifying answers are stored as short keys so they're easy to score and
# chart. Labels are what a human sees. Shared by the form, the agent's tool,
# the scoring rules and the dashboard, so they always agree.
BUDGET_RANGES = {
    "under_25k": "Under ₹25k/month",
    "25k_50k": "₹25k–50k/month",
    "50k_1l": "₹50k–1L/month",
    "1l_plus": "Above ₹1L/month",
    "not_sure": "Not sure yet",
}

TIMELINES = {
    "asap": "Within 2 weeks",
    "this_month": "Within a month",
    "1_3_months": "In 1–3 months",
    "exploring": "Just exploring",
}


@dataclass
class Business:
    name: str
    city: str
    description: str
    services: dict[str, dict]  # key -> {"label": ..., "price": ...}
    meetings: dict  # meeting length, office hours, etc.

    def service_label(self, key: str) -> str:
        return self.services.get(key, {}).get("label", key or "your enquiry")


def load_business(path: Path) -> Business:
    with open(path, encoding="utf-8") as f:
        return Business(**json.load(f))


def load_faq(path: Path) -> list[tuple[str, str]]:
    """Read faq.md: every '## ' heading is a question, the text below is the answer."""
    faq, question, answer = [], None, []
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.startswith("## "):
            if question:
                faq.append((question, " ".join(answer).strip()))
            question, answer = line[3:].strip(), []
        elif question and line.strip():
            answer.append(line.strip())
    if question:
        faq.append((question, " ".join(answer).strip()))
    return faq
