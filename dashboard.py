"""CallBack AI dashboard for the sales team.

  streamlit run dashboard.py

Reads enquiries + meetings (CSV or Google Sheet) and shows how fast leads
are called, what happened, how hot they are, and who to follow up with.
"""

from datetime import datetime, timezone

import pandas as pd
import plotly.express as px
import streamlit as st

from callback_ai.analytics import (
    enquiries_frame,
    enquiries_per_day,
    headline_numbers,
    outcome_counts,
    score_counts,
)
from callback_ai.business import load_business
from callback_ai.config import get_settings
from callback_ai.phone import mask_phone
from callback_ai.storage import MEETINGS, get_store

st.set_page_config(page_title="CallBack AI", page_icon="📞", layout="wide")

# Validated categorical colours. Each chart also labels every bar, so colour
# is never the only way to tell categories apart.
BLUE, ORANGE, YELLOW = "#2a78d6", "#eb6834", "#eda100"
SCORE_COLORS = {"Hot": ORANGE, "Warm": YELLOW, "Cold": BLUE}


def bar_layout(fig, height):
    fig.update_layout(xaxis_title=None, yaxis_title=None, showlegend=False,
                      height=height, bargap=0.4, margin={"l": 0, "r": 30, "t": 10, "b": 0})
    return fig


def format_wait(seconds) -> str:
    if seconds is None:
        return "–"
    return f"{seconds:.0f} sec" if seconds < 120 else f"{seconds / 60:.1f} min"


# ---------- Load data (cached for 30 s so the sheet isn't hit on every click) ----------
settings = get_settings()
business = load_business(settings.business_file)


@st.cache_data(ttl=30)
def load_rows() -> tuple[list[dict], list[dict]]:
    return get_store(settings).read_all(), get_store(settings, MEETINGS).read_all()


try:
    enquiry_rows, meeting_rows = load_rows()
except Exception as error:
    st.error(f"Couldn't read data from **{settings.storage_backend}**: {error}")
    st.stop()

df = enquiries_frame(enquiry_rows, business)

# ---------- Sidebar ----------
with st.sidebar:
    st.header("CallBack AI")
    st.caption(f"{business.name} · data from **{settings.storage_backend}**")
    show_phones = st.toggle("Show full phone numbers", value=False)
    if any(str(r.get("enquiry_id", "")).startswith("SIM-") for r in enquiry_rows):
        st.caption("⚠️ Includes **simulated** leads (from simulate_leads.py).")
    if settings.test_mode:
        st.caption("🧪 Test mode is on: only approved numbers are called.")
    if st.button("Refresh data"):
        load_rows.clear()
        st.rerun()

st.title("Enquiry follow-up")
if df.empty:
    st.info("No enquiries yet. Submit the form, or run `python simulate_leads.py`.")
    st.stop()

phone_col = df["phone"] if show_phones else df["phone"].map(mask_phone)
df = df.assign(phone_display=phone_col)

# ---------- Headline numbers ----------
n = headline_numbers(df)
c1, c2, c3, c4, c5 = st.columns(5)
c1.metric("Total enquiries", n["total"])
c2.metric("Reached", n["reached"], help="Spoke to a person (not voicemail / no answer)")
c3.metric("Meetings booked", n["meetings"])
c4.metric("Hot leads", n["hot"])
c5.metric("Median time to call", format_wait(n["median_seconds_to_call"]),
          help="From form submission to the agent dialling. Faster = more leads reached.")

# ---------- Charts ----------
left, right = st.columns(2)
with left:
    st.subheader("Call outcomes")
    counts = outcome_counts(df)
    fig = px.bar(counts, x="enquiries", y="outcome", orientation="h", text="enquiries",
                 color_discrete_sequence=[BLUE])
    fig.update_traces(textposition="outside", cliponaxis=False,
                      hovertemplate="%{y}: %{x}<extra></extra>")
    fig.update_yaxes(categoryorder="array", categoryarray=list(reversed(counts["outcome"])))
    fig.update_xaxes(showgrid=False, showticklabels=False)
    st.plotly_chart(bar_layout(fig, 40 * len(counts) + 40), use_container_width=True)

with right:
    st.subheader("Lead score")
    st.caption("Only leads we actually spoke to.")
    scores = score_counts(df)
    fig = px.bar(scores, x="leads", y="score", orientation="h", text="leads",
                 color="score", color_discrete_map=SCORE_COLORS)
    fig.update_traces(textposition="outside", cliponaxis=False,
                      hovertemplate="%{y}: %{x} leads<extra></extra>")
    fig.update_yaxes(categoryorder="array", categoryarray=["Cold", "Warm", "Hot"])
    fig.update_xaxes(showgrid=False, showticklabels=False)
    st.plotly_chart(bar_layout(fig, 180), use_container_width=True)
    st.caption("Hot = 6+ points, warm = 3–5, cold = 0–2 (out of 8). Points come from "
               "intent, budget and timeline; see *Why* in the tables below.")

    st.subheader("Enquiries per day")
    per_day = enquiries_per_day(df)
    fig = px.bar(per_day, x="day", y="enquiries", color_discrete_sequence=[BLUE])
    fig.update_traces(hovertemplate="%{x|%a %d %b}: %{y}<extra></extra>")
    fig.update_xaxes(tickformat="%d %b", dtick=86_400_000)
    st.plotly_chart(bar_layout(fig, 200), use_container_width=True)

# ---------- Hot leads ----------
st.subheader("🔥 Hot leads")
hot = df[df["lead_score"] == "hot"]
if hot.empty:
    st.caption("No hot leads yet.")
else:
    st.dataframe(
        hot[["name", "company", "phone_display", "service_label", "budget_label",
             "timeline_label", "meeting_label", "score_reason", "summary"]],
        hide_index=True, use_container_width=True,
        column_config={
            "name": "Name", "company": "Company", "phone_display": "Phone",
            "service_label": "Service", "budget_label": "Budget",
            "timeline_label": "Timeline", "meeting_label": "Meeting",
            "score_reason": st.column_config.TextColumn("Why hot", width="medium"),
            "summary": st.column_config.TextColumn("Call summary", width="large"),
        },
    )

# ---------- Meetings ----------
st.subheader("📅 Upcoming meetings")
now = datetime.now(timezone.utc)
upcoming = sorted((m for m in meeting_rows if datetime.fromisoformat(m["slot_id"]) >= now),
                  key=lambda m: m["slot_id"])
if not upcoming:
    st.caption("No upcoming meetings.")
else:
    st.dataframe(pd.DataFrame(upcoming)[["slot_label", "name", "company", "enquiry_id"]],
                 hide_index=True, use_container_width=True,
                 column_config={"slot_label": "When (IST)", "name": "Name",
                                "company": "Company", "enquiry_id": "Enquiry"})

# ---------- Follow-ups ----------
in_play = df["outcome"].isin(["meeting_booked", "interested", "maybe"])
follow = df[(in_play & (df["questions"] != "")) | (df["outcome"] == "call_later")]
if not follow.empty:
    st.subheader("Needs a human follow-up")
    for row in follow.itertuples():
        note = (f"Call back: {row.callback_time or 'time not given'}"
                if row.outcome == "call_later" else row.questions)
        st.markdown(f"- **{row.name}** ({row.company or 'no company'}, {row.phone_display}): {note}")

# ---------- Everything ----------
with st.expander("All enquiries"):
    st.dataframe(
        df[["submitted_ist", "name", "company", "phone_display", "service_label",
            "outcome_label", "score_label", "budget_label", "timeline_label",
            "seconds_to_call", "summary"]],
        hide_index=True, use_container_width=True,
        column_config={
            "submitted_ist": st.column_config.DatetimeColumn("Submitted (IST)", format="D MMM, h:mm a"),
            "name": "Name", "company": "Company", "phone_display": "Phone",
            "service_label": "Service", "outcome_label": "Outcome", "score_label": "Score",
            "budget_label": "Budget", "timeline_label": "Timeline",
            "seconds_to_call": st.column_config.NumberColumn("Sec to call", format="%d"),
            "summary": st.column_config.TextColumn("Summary", width="large"),
        },
    )
