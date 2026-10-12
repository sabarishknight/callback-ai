"""Meeting slots: generated from office hours in business.json, minus booked ones.

A slot's ID is its start time in ISO format, e.g. "2026-10-14T11:00+05:30".
The agent reads out the friendly label and passes the ID back when booking.
"""

from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from .business import Business

DAY_NAMES = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]


def slot_label(slot_id: str) -> str:
    """'2026-10-14T11:00:00+05:30' -> 'Wednesday 14 October, 11:00 AM'"""
    start = datetime.fromisoformat(slot_id)
    return start.strftime("%A %d %B, %I:%M %p").replace(" 0", " ")


def all_slots(business: Business, now: datetime) -> list[str]:
    """Every slot in office hours over the next few working days."""
    rules = business.meetings
    tz = ZoneInfo(rules["timezone"])
    now = now.astimezone(tz)
    earliest = now + timedelta(hours=rules["min_notice_hours"])
    length = timedelta(minutes=rules["length_minutes"])
    open_h, open_m = map(int, rules["office_hours_start"].split(":"))
    close_h, close_m = map(int, rules["office_hours_end"].split(":"))

    slots, day, working_days_seen = [], now.date(), 0
    while working_days_seen < rules["days_ahead"]:
        if DAY_NAMES[day.weekday()] in rules["working_days"]:
            start = datetime(day.year, day.month, day.day, open_h, open_m, tzinfo=tz)
            close = datetime(day.year, day.month, day.day, close_h, close_m, tzinfo=tz)
            day_has_slots = False
            while start + length <= close:
                if start >= earliest:
                    slots.append(start.isoformat())
                    day_has_slots = True
                start += length
            # Only count days that actually had bookable time left.
            if day_has_slots:
                working_days_seen += 1
        day += timedelta(days=1)
    return slots


def free_slots(business: Business, booked: set[str], now: datetime,
               per_day: int = 3) -> list[str]:
    """A short, varied list to offer on the phone: up to `per_day` per day,
    spread across morning and afternoon, skipping booked ones."""
    by_day: dict[str, list[str]] = {}
    for slot in all_slots(business, now):
        if slot not in booked:
            by_day.setdefault(slot[:10], []).append(slot)

    offered = []
    for slots in by_day.values():
        if len(slots) <= per_day:
            offered.extend(slots)
        else:
            step = len(slots) / per_day  # pick evenly spaced times
            offered.extend(slots[int(i * step)] for i in range(per_day))
    return offered


def is_bookable(business: Business, slot_id: str, booked: set[str], now: datetime) -> bool:
    return slot_id in all_slots(business, now) and slot_id not in booked
