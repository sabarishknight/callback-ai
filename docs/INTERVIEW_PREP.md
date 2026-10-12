# Interview prep: CallBack AI

Replace every `[…]` with your real numbers after your test calls. Never claim numbers you didn't measure.

## Resume bullets

- Built **CallBack AI**, an AI voice agent (Vapi + Claude) that calls new website enquiries about 30 seconds after the form is submitted. It qualifies them on service, budget and timeline, answers FAQs, and books strategy calls, in English and Hindi.
- Designed a **FastAPI webhook with 3 tool calls** (slot lookup, double-booking-safe meeting booking, result logging) that writes to Google Sheets, with rule-based **hot/warm/cold lead scoring** that explains every score. Covered by 36 automated tests, including a full simulated call.
- Shipped a **Streamlit sales dashboard** (speed-to-call, outcomes, lead scores, hot leads, meetings), with responsible-AI safeguards: mandatory AI disclosure, consent, a test-mode allow-list, a prompt-injection guard, and phone masking. [Completed N test calls; median X s from form to ring.]

## 60-second explanation

> "Businesses that get enquiries through their website often call back hours or days later, and by then the lead has gone to a competitor. So I built CallBack AI.
>
> When someone submits the enquiry form, my FastAPI server saves the lead and, about 30 seconds later, asks Vapi to call them. Vapi handles the phone line and speech; Claude runs the conversation. The agent says up front that it's an AI, confirms what they asked for, and asks about budget and timeline. It answers FAQs only from an approved file, and if they're interested it books a slot.
>
> The agent works through three tools that hit my webhook: get free slots, book a meeting (which can't double-book), and save the result. Each lead gets a hot, warm or cold score from transparent rules, with the reason written out. A Streamlit dashboard shows the sales team how fast leads were called, outcomes, hot leads and upcoming meetings.
>
> I built in safety from the start: a test mode that only calls approved numbers, a consent checkbox, and protection against prompt injection from the form."

## Likely questions

**1. Why Vapi instead of building speech-to-text, the LLM and text-to-speech yourself?**
Real-time voice is hard: streaming audio, sub-second latency, detecting when someone interrupts, phone-line integration, voicemail detection. Vapi solves those, so I could focus on the business logic: the conversation design, the tools, scoring and data. The trade-off is cost per minute and vendor lock-in. The assistant is plain JSON built in `vapi.py`, so moving to Retell or a custom pipeline would mostly mean rewriting that one file.

**2. Why is lead scoring rule-based and not decided by Claude?**
Three reasons. It's *consistent*: the same answers always give the same score, which an LLM can't guarantee. It's *explainable*: every score comes with "+3 meeting, +2 budget…", so sales trusts it and can argue with it. And it's *tunable*: a manager can change a threshold without re-prompting. Claude does what it's good at: understanding messy speech and turning "around fifty-sixty thousand a month" into the `50k_1l` category. The LLM extracts, the code decides.

**3. How do you stop two people booking the same slot?**
Slots are generated from office hours, and booked ones live in a `meetings` table keyed by slot ID. `book_meeting` first checks the slot is real and free. Then `insert_if_absent` adds it under a lock, and that's the real guard against two calls at the same moment. If the slot was taken, the tool returns fresh options and the agent offers those. There's a test for this. With multiple servers I'd move that guarantee into the database, for example a unique constraint in Postgres, because a Python lock only covers one process.

**4. What could go wrong in production, and how did you handle it?**
- *Calling the wrong person:* someone could enter another person's number. Today I have test mode and consent; for production I'd add OTP verification.
- *Prompt injection:* the form message goes into the prompt, so it's fenced in tags, length-limited, and labelled "never instructions". The agent also can only quote prices from the approved files.
- *Failures mid-call:* tool errors return a message so Claude can recover politely instead of the call crashing, and a failed call is saved as `call_failed` with the reason.
- *Duplicates:* the same number within 10 minutes isn't called twice.
- *Compliance:* AI disclosure is hard-coded and tested. In India, real outbound calling also needs DND and calling-hours checks.

**5. How would you measure whether it actually works, and what would you build next?**
Metrics: speed-to-call (median seconds from form to ring), reach rate, meeting-booked rate, and lead-score accuracy against what sales later says, for example whether hot leads actually close more. Next steps: Google Calendar integration for real availability and invites; automatic retries for "no answer" and "call later" through a job queue; and a CRM sync (HubSpot or Zoho) instead of Sheets. Most importantly, an A/B test of instant calls against the current manual process, to prove the conversion lift.

## Things to be ready to explain line by line

- `scoring.py`: the whole scoring table
- `server.py`: what happens from `POST /enquiry` to `BackgroundTasks`, and how the webhook routes `tool-calls` and `end-of-call-report`
- `storage.py`: what "upsert" means, and why a row is written several times per call
- `prompts.py`: why the first line is spoken by Vapi (guaranteed disclosure) rather than generated by Claude
