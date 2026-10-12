"""Talk to the Vapi REST API: build an assistant config and start calls.

Vapi handles the hard real-time parts (phone line, speech-to-text, Claude,
text-to-speech, interruptions). We just describe the assistant as JSON.
API reference: https://docs.vapi.ai/api-reference
"""

from datetime import datetime
from urllib.parse import urlencode

import requests

from .business import BUDGET_RANGES, TIMELINES, Business
from .config import Settings
from .leads import Enquiry
from .prompts import build_system_prompt, spoken_lines

VAPI_BASE_URL = "https://api.vapi.ai"

# Voice (text-to-speech) and transcriber (speech-to-text) per language.
SPEECH_CONFIG = {
    "english": {
        "voice": {"provider": "azure", "voiceId": "en-IN-NeerjaNeural"},
        "transcriber": {"provider": "deepgram", "model": "nova-2", "language": "en-IN"},
    },
    "hindi": {
        "voice": {"provider": "azure", "voiceId": "hi-IN-SwaraNeural"},
        "transcriber": {"provider": "deepgram", "model": "nova-2", "language": "hi"},
    },
}


def _tools(business: Business) -> list[dict]:
    """The three tools Claude can call. Vapi forwards each call to our webhook."""
    return [
        {
            "type": "function",
            "function": {
                "name": "get_available_slots",
                "description": "Get the free times for a strategy call over the next "
                               "few working days. Call this before offering times.",
                "parameters": {"type": "object", "properties": {}},
            },
        },
        {
            "type": "function",
            "function": {
                "name": "book_meeting",
                "description": "Book one of the slots returned by get_available_slots, "
                               "after the caller has agreed to that time.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "slot_id": {"type": "string",
                                    "description": "The slot_id exactly as returned, "
                                                   "e.g. 2026-10-14T11:00:00+05:30"},
                    },
                    "required": ["slot_id"],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "save_call_result",
                "description": "Save what you learned. Call exactly once, just before "
                               "ending the call.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "interest": {
                            "type": "string",
                            "enum": ["yes", "maybe", "no", "call_later"],
                            "description": "Are they interested in working with us?",
                        },
                        "service_type": {
                            "type": "string",
                            "enum": list(business.services),
                            "description": "The service they need.",
                        },
                        "budget_range": {
                            "type": "string",
                            "enum": list(BUDGET_RANGES),
                            "description": "Their budget: " + ", ".join(
                                f"{k} = {v}" for k, v in BUDGET_RANGES.items()),
                        },
                        "timeline": {
                            "type": "string",
                            "enum": list(TIMELINES),
                            "description": "When they want to start: " + ", ".join(
                                f"{k} = {v}" for k, v in TIMELINES.items()),
                        },
                        "callback_time": {
                            "type": "string",
                            "description": "If they asked to be called later, when.",
                        },
                        "questions": {
                            "type": "string",
                            "description": "Questions you couldn't answer, or concerns "
                                           "the team should follow up on.",
                        },
                        "summary": {
                            "type": "string",
                            "description": "One or two sentence summary of the call, in English.",
                        },
                    },
                    "required": ["interest", "summary"],
                },
            },
        },
    ]


def webhook_url(settings: Settings, enquiry: Enquiry) -> str:
    """Our webhook URL, with the enquiry ID in the query string.

    That's how the webhook knows which enquiry a call belongs to, without
    relying on the AI to remember an ID.
    """
    query = urlencode({"enquiry_id": enquiry.enquiry_id})
    return f"{settings.public_base_url}/vapi/webhook?{query}"


def build_assistant(business: Business, faq: list[tuple[str, str]], enquiry: Enquiry,
                    settings: Settings, now: datetime, phone_call: bool = True) -> dict:
    """The full assistant definition for one enquiry, as Vapi expects it."""
    lines = spoken_lines(business, enquiry, settings.assistant_name)
    speech = SPEECH_CONFIG.get(enquiry.language, SPEECH_CONFIG["english"])

    # Where Vapi sends tool calls and the end-of-call report. The secret header
    # lets our webhook reject requests that didn't come from Vapi.
    server = {"url": webhook_url(settings, enquiry)}
    if settings.webhook_secret:
        server["headers"] = {"x-vapi-secret": settings.webhook_secret}

    tools = [{**tool, "server": server} for tool in _tools(business)]
    tools.append({"type": "endCall"})  # built-in Vapi tool that hangs up

    assistant = {
        # Vapi limits names to 40 characters.
        "name": f"CallBack AI - {enquiry.name}"[:40],
        "firstMessage": lines["first_message"],
        "model": {
            "provider": "anthropic",
            "model": settings.claude_model,
            "temperature": 0.3,  # friendly but consistent
            "maxTokens": 250,  # short spoken replies
            "messages": [{
                "role": "system",
                "content": build_system_prompt(business, faq, enquiry,
                                               settings.assistant_name, now),
            }],
            "tools": tools,
        },
        "voice": speech["voice"],
        "transcriber": speech["transcriber"],
        "endCallMessage": lines["end_call"],
        "maxDurationSeconds": 360,  # hard stop at 6 minutes
        "silenceTimeoutSeconds": 30,
        "server": server,
        # Only send us the events we use.
        "serverMessages": ["tool-calls", "end-of-call-report"],
        "metadata": {"enquiry_id": enquiry.enquiry_id},
    }

    if phone_call:
        # Voicemail detection only makes sense on a real phone line.
        assistant["voicemailDetection"] = {"provider": "vapi"}
        assistant["voicemailMessage"] = lines["voicemail"]

    return assistant


def _headers(settings: Settings) -> dict:
    if not settings.vapi_api_key:
        raise RuntimeError("VAPI_API_KEY is missing from .env")
    return {"Authorization": f"Bearer {settings.vapi_api_key}",
            "Content-Type": "application/json"}


def _post(path: str, body: dict, settings: Settings) -> dict:
    response = requests.post(f"{VAPI_BASE_URL}{path}", json=body,
                             headers=_headers(settings), timeout=30)
    if not response.ok:
        # Vapi's error body says exactly which field it didn't like.
        raise RuntimeError(f"Vapi {path} failed ({response.status_code}): {response.text}")
    return response.json()


def start_phone_call(assistant: dict, number: str, customer_name: str,
                     settings: Settings) -> dict:
    """Ask Vapi to dial `number` using this assistant. Returns the call object."""
    if not settings.vapi_phone_number_id:
        raise RuntimeError("VAPI_PHONE_NUMBER_ID is missing from .env")
    return _post("/call", {
        "phoneNumberId": settings.vapi_phone_number_id,
        "customer": {"number": number, "name": customer_name},
        "assistant": assistant,
    }, settings)


def create_saved_assistant(assistant: dict, settings: Settings) -> dict:
    """Save an assistant in your Vapi dashboard (used for free browser test calls)."""
    return _post("/assistant", assistant, settings)
