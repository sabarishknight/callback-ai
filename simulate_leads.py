"""Fill the store with SIMULATED enquiries and call results.

Lets you build and demo the dashboard before making any real calls.
Simulated enquiry IDs start with "SIM-" so they're never confused with real ones.

  python simulate_leads.py            # add 20 simulated leads
  python simulate_leads.py --clear    # wipe ALL enquiries + meetings first
"""

import argparse
import random
from datetime import datetime, timedelta, timezone

from callback_ai.business import load_business
from callback_ai.config import get_settings
from callback_ai.scheduling import free_slots, slot_label
from callback_ai.storage import MEETINGS, get_store

PEOPLE = [
    ("Ravi Kumar", "Kumar Sweets"), ("Neha Gupta", "UrbanNest Interiors"),
    ("Arjun Nair", "FitZone Gyms"), ("Shalini Reddy", "Bloom Dental Clinic"),
    ("Imran Sheikh", "Sheikh Realty"), ("Ananya Iyer", "Little Steps Preschool"),
    ("Vikram Joshi", "CloudLedger SaaS"), ("Pooja Malhotra", "Saree Story"),
    ("Karthik Rao", "Rao's Filter Coffee"), ("Meera Pillai", "Coastal Homestays"),
    ("Rahul Verma", "Verma Auto Spares"), ("Divya Menon", "GlowUp Skincare"),
    ("Suresh Patil", "Patil Agro Exports"), ("Aisha Khan", "BrightMinds Coaching"),
    ("Nikhil Bansal", "Bansal Electronics"), ("Lakshmi Hegde", "Hegde Ayurveda"),
    ("Farhan Ali", "Spice Route Restaurant"), ("Tanya Sethi", "Sethi Legal"),
    ("Manoj Shetty", "Shetty Builders"), ("Priya Das", "Pawsome Pet Store"),
]

MESSAGES = {
    "social_media": "We post on Instagram but get very little engagement.",
    "performance_ads": "Want more leads from Google ads, current cost per lead is too high.",
    "seo": "Our website doesn't show up on Google for our main services.",
    "website": "Need a new website, the current one is slow and outdated.",
    "other": "Looking for help with WhatsApp marketing.",
}

# (interest, ended_reason, books_meeting, weight)
SCENARIOS = [
    ("yes", "assistant-ended-call", True, 30),
    ("yes", "assistant-ended-call", False, 12),
    ("maybe", "assistant-ended-call", False, 12),
    ("call_later", "assistant-ended-call", False, 10),
    ("no", "assistant-ended-call", False, 12),
    ("", "customer-did-not-answer", False, 10),
    ("", "voicemail", False, 8),
    ("", "customer-ended-call", False, 6),
]

QUESTIONS = [
    "Asked if we have worked with other clinics before.",
    "Wants to know if ad spend can start below ₹20k/month.",
    "Asked whether the 3-month minimum can be reduced to 1 month.",
    "", "", "",
]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--clear", action="store_true", help="delete all enquiries + meetings first")
    parser.add_argument("--seed", type=int, default=11, help="same seed = same results")
    args = parser.parse_args()

    settings = get_settings()
    business = load_business(settings.business_file)
    enquiries, meetings = get_store(settings), get_store(settings, MEETINGS)
    rng = random.Random(args.seed)
    now = datetime.now(timezone.utc)

    if args.clear:
        enquiries.clear()
        meetings.clear()

    open_slots = free_slots(business, {m["slot_id"] for m in meetings.read_all()}, now, per_day=8)
    services = [s for s in business.services if s != "other"] + ["other"]

    for i, (name, company) in enumerate(PEOPLE, start=1):
        enquiry_id = f"SIM-{i:03d}"
        interest, ended_reason, books, _ = rng.choices(
            SCENARIOS, weights=[s[3] for s in SCENARIOS])[0]
        service = rng.choices(services, weights=[3, 3, 2, 2, 1])[0]
        submitted = now - timedelta(days=rng.randint(0, 6), minutes=rng.randint(5, 600))
        wait = rng.randint(32, 95)  # seconds from form to call
        reached = interest != ""

        # Interested leads lean towards bigger budgets and sooner timelines.
        if interest == "yes":
            budget = rng.choice(["50k_1l", "1l_plus", "50k_1l", "25k_50k", "not_sure"])
            timeline = rng.choice(["asap", "this_month", "this_month", "1_3_months"])
        else:
            budget = rng.choice(["under_25k", "25k_50k", "not_sure", "50k_1l"])
            timeline = rng.choice(["1_3_months", "exploring", "this_month"])

        slot = ""
        if books and open_slots:
            slot = open_slots.pop(rng.randrange(len(open_slots)))
            meetings.insert_if_absent(slot, {
                "slot_label": slot_label(slot), "enquiry_id": enquiry_id,
                "name": name, "company": company, "booked_at": now.isoformat(timespec="seconds"),
            })

        first = name.split()[0]
        label = business.service_label(service).lower()
        summary = {
            "yes": f"{first} wants help with {label} and "
                   + (f"booked a strategy call for {slot_label(slot)}." if slot
                      else "asked for a proposal by email first."),
            "maybe": f"{first} is comparing agencies for {label}; will decide after seeing pricing.",
            "call_later": f"{first} was in a meeting and asked us to call back this evening.",
            "no": f"{first} decided to handle {label} in-house for now.",
            "": "",
        }[interest]

        enquiries.upsert(enquiry_id, {
            "submitted_at": submitted.isoformat(timespec="seconds"),
            "name": name,
            "phone": f"+9155555{i:05d}",  # fake, unroutable numbers
            "company": company,
            "service_requested": service,
            "message": MESSAGES[service],
            "language": rng.choice(["english", "english", "english", "hindi"]),
            "status": "call_ended",
            "call_id": f"sim-call-{i:03d}",
            "called_at": (submitted + timedelta(seconds=wait)).isoformat(timespec="seconds"),
            "seconds_to_call": wait,
            "interest": interest,
            "service_type": service if reached else "",
            "budget_range": budget if interest in ("yes", "maybe", "call_later") else "",
            "timeline": timeline if interest in ("yes", "maybe", "call_later") else "",
            "meeting_slot": slot,
            "callback_time": "Today after 6 pm" if interest == "call_later" else "",
            "questions": rng.choice(QUESTIONS) if interest in ("yes", "maybe") else "",
            "summary": summary,
            "ended_reason": ended_reason,
            "duration_sec": rng.randint(60, 210) if reached else rng.randint(0, 25),
        })
        row = enquiries.get(enquiry_id)
        print(f"{enquiry_id} {name:<16} -> {row['outcome']:<15} {row['lead_score'] or '-'}")

    print(f"\nSaved {len(PEOPLE)} simulated leads to {settings.storage_backend}.")


if __name__ == "__main__":
    main()
