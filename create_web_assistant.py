"""Save an assistant in your Vapi dashboard so you can talk to it in the
browser for FREE (no phone number, no call charges). Best first test.

  python create_web_assistant.py
  python create_web_assistant.py --service seo --language hindi --name "Rahul Verma"

It creates a test enquiry (ID starting with WEB-) so the webhook can save the
results, then prints a link. Open it and press "Talk to Assistant".
"""

import argparse
import json
from datetime import datetime, timezone

from callback_ai.business import load_business, load_faq
from callback_ai.config import get_settings
from callback_ai.leads import Enquiry, now_iso
from callback_ai.prompts import LANGUAGE_LINES
from callback_ai.storage import get_store
from callback_ai.vapi import build_assistant, create_saved_assistant


def main():
    settings = get_settings()
    business = load_business(settings.business_file)

    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--name", default="Priya Sharma")
    parser.add_argument("--company", default="Chai Corner Cafes")
    parser.add_argument("--service", default="social_media", choices=list(business.services))
    parser.add_argument("--message", default="We run 3 cafes and want more weekday customers.")
    parser.add_argument("--language", default="english", choices=list(LANGUAGE_LINES))
    parser.add_argument("--print-only", action="store_true",
                        help="print the assistant JSON instead of sending it to Vapi")
    args = parser.parse_args()

    now = datetime.now(timezone.utc)
    enquiry = Enquiry(
        enquiry_id=f"WEB-{now:%Y%m%d-%H%M%S}", name=args.name, phone="",
        company=args.company, service_requested=args.service,
        message=args.message, language=args.language,
    )
    assistant = build_assistant(business, load_faq(settings.faq_file), enquiry,
                                settings, now, phone_call=False)

    if args.print_only:
        print(json.dumps(assistant, indent=2, ensure_ascii=False))
        return

    if not settings.public_base_url:
        print("Note: PUBLIC_BASE_URL is empty, so tools (slots, booking, saving) won't "
              "work. The conversation itself will.\n")

    # Save the test enquiry so the webhook has a row to fill in.
    get_store(settings).upsert(enquiry.enquiry_id, {
        "submitted_at": now_iso(), "name": enquiry.name, "company": enquiry.company,
        "service_requested": enquiry.service_requested, "message": enquiry.message,
        "language": enquiry.language, "status": "web_test",
    })

    saved = create_saved_assistant(assistant, settings)
    print(f"Created test enquiry {enquiry.enquiry_id} and its assistant.")
    print(f"Open: https://dashboard.vapi.ai/assistants/{saved['id']}")
    print("Click 'Talk to Assistant' and play the customer.")


if __name__ == "__main__":
    main()
