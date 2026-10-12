"""All settings live here. Secrets come from the .env file, never from code.

Every other module calls get_settings() instead of reading os.environ
directly, so there is exactly one place to look when a setting is wrong.
"""

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

# The project folder (one level above this file's folder).
PROJECT_ROOT = Path(__file__).resolve().parent.parent

# Load .env into environment variables. Values that are already set in the
# real environment (e.g. on Render) win over the file.
load_dotenv(PROJECT_ROOT / ".env")


def _env(name: str, default: str = "") -> str:
    return os.getenv(name, default).strip()


def _env_bool(name: str, default: bool) -> bool:
    value = _env(name)
    if not value:
        return default
    return value.lower() in ("1", "true", "yes", "on")


def _env_list(name: str) -> list[str]:
    """Turn 'a, b ,c' into ['a', 'b', 'c']."""
    return [item.strip() for item in _env(name).split(",") if item.strip()]


def _env_path(name: str, default: str) -> Path:
    """Relative paths are resolved from the project folder, not from wherever
    you happen to run the command."""
    path = Path(_env(name, default))
    return path if path.is_absolute() else PROJECT_ROOT / path


@dataclass(frozen=True)
class Settings:
    # Vapi
    vapi_api_key: str
    vapi_phone_number_id: str
    claude_model: str

    # Server (enquiry form + webhook)
    public_base_url: str
    webhook_secret: str
    call_delay_seconds: int
    duplicate_window_minutes: int

    # The agent's name (the business name comes from business.json)
    assistant_name: str

    # Safety
    test_mode: bool
    allowed_numbers: list[str]

    # Storage
    storage_backend: str  # "csv" or "sheets"
    data_dir: Path  # where the CSV files go
    google_sheet_id: str
    google_credentials_file: Path
    google_credentials_json: str

    # The demo business
    business_file: Path
    faq_file: Path


def get_settings() -> Settings:
    """Read settings fresh each time (cheap, and lets tests change env vars)."""
    return Settings(
        vapi_api_key=_env("VAPI_API_KEY"),
        vapi_phone_number_id=_env("VAPI_PHONE_NUMBER_ID"),
        claude_model=_env("CLAUDE_MODEL", "claude-haiku-4-5-20251001"),
        public_base_url=_env("PUBLIC_BASE_URL").rstrip("/"),
        webhook_secret=_env("VAPI_WEBHOOK_SECRET"),
        call_delay_seconds=int(_env("CALL_DELAY_SECONDS", "30")),
        duplicate_window_minutes=int(_env("DUPLICATE_WINDOW_MINUTES", "10")),
        assistant_name=_env("ASSISTANT_NAME", "Asha"),
        test_mode=_env_bool("TEST_MODE", True),  # safe by default
        allowed_numbers=_env_list("ALLOWED_NUMBERS"),
        storage_backend=_env("STORAGE_BACKEND", "csv").lower(),
        data_dir=_env_path("DATA_DIR", "data"),
        google_sheet_id=_env("GOOGLE_SHEET_ID"),
        google_credentials_file=_env_path(
            "GOOGLE_CREDENTIALS_FILE", "credentials/service-account.json"
        ),
        google_credentials_json=_env("GOOGLE_CREDENTIALS_JSON"),
        business_file=_env_path("BUSINESS_FILE", "data/business.json"),
        faq_file=_env_path("FAQ_FILE", "data/faq.md"),
    )
