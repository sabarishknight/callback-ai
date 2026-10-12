"""Save data to a local CSV or to Google Sheets. Two tables (= two sheet tabs):

  enquiries - one row per enquiry, keyed by enquiry_id
  meetings  - one row per booked slot, keyed by slot_id (prevents double booking)

An enquiry row is written several times: when the form is submitted, when
the call starts, when the agent saves answers, and after hang-up. So we
"upsert": update the row if it exists, otherwise add it.
"""

import csv
import json
import threading
from collections.abc import Callable
from dataclasses import dataclass
from functools import lru_cache

from .config import Settings
from .leads import ENQUIRY_FIELDS, MEETING_FIELDS, derive_fields, now_iso


@dataclass(frozen=True)
class Table:
    name: str  # CSV file name / sheet tab name
    fields: tuple[str, ...]  # column order
    key: str  # the column that identifies a row
    derive: Callable[[dict], dict] | None = None  # recalculated columns


ENQUIRIES = Table("enquiries", tuple(ENQUIRY_FIELDS), "enquiry_id", derive_fields)
MEETINGS = Table("meetings", tuple(MEETING_FIELDS), "slot_id")


def merge_row(table: Table, existing: dict, updates: dict,
              defaults: dict | None = None) -> dict:
    """Combine an existing row with new values.

    - `updates` overwrite, but empty values never wipe out existing data.
    - `defaults` are only used for fields that are still empty.
    """
    row = {f: existing.get(f, "") for f in table.fields}
    for key, value in updates.items():
        if key in row and value not in (None, ""):
            row[key] = value
    for key, value in (defaults or {}).items():
        if key in row and row[key] in (None, "") and value not in (None, ""):
            row[key] = value
    if "updated_at" in row:
        row["updated_at"] = now_iso()
    return table.derive(row) if table.derive else row


# One lock per table: the web server can handle two events at the same time.
_LOCKS = {ENQUIRIES.name: threading.Lock(), MEETINGS.name: threading.Lock()}


class CsvStore:
    """Rows in data/<table>.csv. Zero setup, great for local testing."""

    def __init__(self, settings: Settings, table: Table):
        self.table = table
        self.path = settings.data_dir / f"{table.name}.csv"

    def read_all(self) -> list[dict]:
        if not self.path.exists():
            return []
        with open(self.path, encoding="utf-8", newline="") as f:
            return list(csv.DictReader(f))

    def get(self, key: str) -> dict | None:
        return next((r for r in self.read_all() if r[self.table.key] == key), None)

    def _write_all(self, rows: list[dict]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with open(self.path, "w", encoding="utf-8", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=self.table.fields, extrasaction="ignore")
            writer.writeheader()
            writer.writerows(rows)

    def upsert(self, key: str, updates: dict, defaults: dict | None = None) -> dict:
        with _LOCKS[self.table.name]:
            rows = self.read_all()
            index = next((i for i, r in enumerate(rows) if r[self.table.key] == key), None)
            existing = rows[index] if index is not None else {self.table.key: key}
            row = merge_row(self.table, existing, updates, defaults)
            if index is None:
                rows.append(row)
            else:
                rows[index] = row
            self._write_all(rows)
            return row

    def insert_if_absent(self, key: str, row: dict) -> bool:
        """Add a row only if the key is new. Returns False if it already existed.
        Used to book a meeting slot without ever double-booking it."""
        with _LOCKS[self.table.name]:
            rows = self.read_all()
            if any(r[self.table.key] == key for r in rows):
                return False
            rows.append(merge_row(self.table, {self.table.key: key}, row))
            self._write_all(rows)
            return True

    def clear(self) -> None:
        if self.path.exists():
            self.path.unlink()


@lru_cache(maxsize=8)
def _open_worksheet(sheet_id: str, credentials_json: str, credentials_file: str,
                    tab_name: str, fields: tuple[str, ...]):
    """Log in with the service account and open (or create) a tab.
    Cached so we don't re-authenticate on every webhook request."""
    import gspread  # imported here so CSV mode works without Google libraries

    if credentials_json:
        client = gspread.service_account_from_dict(json.loads(credentials_json))
    else:
        client = gspread.service_account(filename=credentials_file)
    spreadsheet = client.open_by_key(sheet_id)
    try:
        worksheet = spreadsheet.worksheet(tab_name)
    except gspread.WorksheetNotFound:
        worksheet = spreadsheet.add_worksheet(tab_name, rows=1000, cols=len(fields))
    if worksheet.row_values(1) != list(fields):
        worksheet.update(range_name="A1", values=[list(fields)])  # header row
    return worksheet


class SheetsStore:
    """Rows in a tab of a Google Sheet that is shared with the service account."""

    def __init__(self, settings: Settings, table: Table):
        if not settings.google_sheet_id:
            raise RuntimeError("STORAGE_BACKEND=sheets but GOOGLE_SHEET_ID is empty")
        self.table = table
        self.worksheet = _open_worksheet(
            settings.google_sheet_id, settings.google_credentials_json,
            str(settings.google_credentials_file), table.name, table.fields,
        )

    def read_all(self) -> list[dict]:
        # numericise_ignore keeps every value as text, same as the CSV store.
        return self.worksheet.get_all_records(numericise_ignore=["all"])

    def get(self, key: str) -> dict | None:
        row_number = self._find(key)
        if not row_number:
            return None
        return dict(zip(self.table.fields, self.worksheet.row_values(row_number)))

    def _find(self, key: str) -> int | None:
        keys = self.worksheet.col_values(self.table.fields.index(self.table.key) + 1)
        return keys.index(key) + 1 if key in keys else None  # 1-based, header = row 1

    def _write(self, row_number: int | None, row: dict) -> None:
        values = [str(row[f]) for f in self.table.fields]
        if row_number:
            self.worksheet.update(range_name=f"A{row_number}", values=[values])
        else:
            self.worksheet.append_row(values, value_input_option="RAW")

    def upsert(self, key: str, updates: dict, defaults: dict | None = None) -> dict:
        with _LOCKS[self.table.name]:
            row_number = self._find(key)
            existing = {self.table.key: key}
            if row_number:
                existing = dict(zip(self.table.fields, self.worksheet.row_values(row_number)))
            row = merge_row(self.table, existing, updates, defaults)
            self._write(row_number, row)
            return row

    def insert_if_absent(self, key: str, row: dict) -> bool:
        with _LOCKS[self.table.name]:
            if self._find(key):
                return False
            self._write(None, merge_row(self.table, {self.table.key: key}, row))
            return True

    def clear(self) -> None:
        self.worksheet.clear()
        self.worksheet.update(range_name="A1", values=[list(self.table.fields)])


def get_store(settings: Settings, table: Table = ENQUIRIES):
    if settings.storage_backend == "sheets":
        return SheetsStore(settings, table)
    if settings.storage_backend == "csv":
        return CsvStore(settings, table)
    raise RuntimeError(f"Unknown STORAGE_BACKEND {settings.storage_backend!r} "
                       "(use 'csv' or 'sheets')")
