"""Google Sheets API client.

Authenticates via a service account key file pointed to by
GOOGLE_APPLICATION_CREDENTIALS, and writes rows to the spreadsheet
identified by GOOGLE_SHEET_ID.

Column order for each tab is driven by config/sheet_schema.yaml so callers
pass a plain dict and never need to know the positional order themselves.
"""

import json
import os
import sys
from pathlib import Path
from typing import Any, Optional

import yaml
from dotenv import load_dotenv
from google.oauth2 import service_account
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError

load_dotenv()

_SCOPES = ["https://www.googleapis.com/auth/spreadsheets"]
_SCHEMA_PATH = Path(__file__).parents[2] / "config" / "sheet_schema.yaml"

# Module-level cache so the YAML file is only read once per process.
_schema_cache: Optional[dict] = None
# Print credential diagnostics only once per process.
_creds_logged = False


def _load_schema() -> dict:
    global _schema_cache
    if _schema_cache is None:
        with open(_SCHEMA_PATH) as f:
            _schema_cache = yaml.safe_load(f)["spreadsheet"]
    return _schema_cache


def _column_names(sheet_name: str) -> list[str]:
    """Return the ordered list of column names for a sheet tab."""
    schema = _load_schema()
    if sheet_name not in schema:
        raise KeyError(
            f"Sheet '{sheet_name}' not found in sheet_schema.yaml. "
            f"Available tabs: {list(schema.keys())}"
        )
    return [col["name"] for col in schema[sheet_name]["columns"]]


def _log_credential_info(creds_path: str) -> None:
    """Print credential file diagnostics for CI log. Never prints private_key content."""
    p = Path(creds_path)
    exists = p.exists()
    print(f"[google_sheets] GOOGLE_APPLICATION_CREDENTIALS={creds_path!r} exists={exists}")
    if not exists:
        print("[google_sheets] Credentials file NOT FOUND — all sheet operations will fail", file=sys.stderr)
        return
    size = p.stat().st_size
    print(f"[google_sheets] Credentials file size: {size} bytes")
    try:
        data = json.loads(p.read_text())
        email = data.get("client_email", "(missing)")
        project = data.get("project_id", "(missing)")
        print(f"[google_sheets] Service account email: {email}")
        print(f"[google_sheets] Project ID: {project}")
    except Exception as exc:
        print(f"[google_sheets] Could not parse credentials JSON: {exc}", file=sys.stderr)


def _get_service():
    global _creds_logged
    creds_path = os.environ["GOOGLE_APPLICATION_CREDENTIALS"]
    if not _creds_logged:
        _log_credential_info(creds_path)
        _creds_logged = True
    credentials = service_account.Credentials.from_service_account_file(
        creds_path,
        scopes=_SCOPES,
    )
    return build("sheets", "v4", credentials=credentials, cache_discovery=False)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def append_row(sheet_name: str, row: dict) -> bool:
    """Append a single row to a named sheet tab.

    Column order is read from config/sheet_schema.yaml. Keys in `row` that
    are not in the schema are silently ignored. Schema columns missing from
    `row` are written as empty strings.

    Args:
        sheet_name: Tab name matching a key under `spreadsheet:` in sheet_schema.yaml.
        row: Dict mapping column names to values.

    Returns:
        True on success, False on any error (details printed to stderr).
    """
    # --- resolve column order from schema ---
    try:
        columns = _column_names(sheet_name)
    except KeyError as exc:
        print(f"[google_sheets] Schema error: {exc}", file=sys.stderr)
        return False
    except (FileNotFoundError, yaml.YAMLError) as exc:
        print(f"[google_sheets] Failed to load sheet_schema.yaml: {exc}", file=sys.stderr)
        return False

    # Build a flat list in schema column order; missing keys become "".
    values = [
        "" if row.get(col) is None else str(row[col])
        for col in columns
    ]

    # --- validate env vars before touching the network ---
    sheet_id = os.environ.get("GOOGLE_SHEET_ID")
    if not sheet_id:
        print("[google_sheets] GOOGLE_SHEET_ID is not set.", file=sys.stderr)
        return False

    if not os.environ.get("GOOGLE_APPLICATION_CREDENTIALS"):
        print("[google_sheets] GOOGLE_APPLICATION_CREDENTIALS is not set.", file=sys.stderr)
        return False

    # --- call the API ---
    try:
        service = _get_service()
        service.spreadsheets().values().append(
            spreadsheetId=sheet_id,
            range=f"{sheet_name}!A1",
            valueInputOption="RAW",
            insertDataOption="INSERT_ROWS",
            body={"values": [values]},
        ).execute()
        print(f"[google_sheets] append_row OK → tab='{sheet_name}'")
        return True
    except FileNotFoundError as exc:
        print(f"[google_sheets] Credentials file not found: {exc}", file=sys.stderr)
        return False
    except HttpError as exc:
        print(f"[google_sheets] Sheets API error {exc.status_code}: {exc.reason}", file=sys.stderr)
        return False
    except Exception as exc:
        print(f"[google_sheets] Unexpected error in append_row: {type(exc).__name__}: {exc}", file=sys.stderr)
        return False


def append_rows(tab: str, rows: list[list[Any]]) -> dict:
    """Append pre-ordered rows (list of lists) to a sheet tab.

    Lower-level than append_row — callers must supply values in the correct
    column order themselves. Used by existing jobs written before append_row
    was introduced.
    """
    sheet_id = os.environ["GOOGLE_SHEET_ID"]
    service = _get_service()
    result = (
        service.spreadsheets()
        .values()
        .append(
            spreadsheetId=sheet_id,
            range=f"{tab}!A1",
            valueInputOption="USER_ENTERED",
            insertDataOption="INSERT_ROWS",
            body={"values": rows},
        )
        .execute()
    )
    return result


def ensure_headers(sheet_name: str) -> bool:
    """Write column header row to a sheet tab if it is currently empty.

    Returns True if headers are already present or were just written.
    Returns False on any error (details printed to stderr).
    """
    try:
        columns = _column_names(sheet_name)
    except KeyError as exc:
        print(f"[google_sheets] Schema error in ensure_headers: {exc}", file=sys.stderr)
        return False

    sheet_id = os.environ.get("GOOGLE_SHEET_ID")
    if not sheet_id:
        print("[google_sheets] GOOGLE_SHEET_ID is not set.", file=sys.stderr)
        return False
    if not os.environ.get("GOOGLE_APPLICATION_CREDENTIALS"):
        print("[google_sheets] GOOGLE_APPLICATION_CREDENTIALS is not set.", file=sys.stderr)
        return False

    try:
        service = _get_service()
        result = (
            service.spreadsheets()
            .values()
            .get(spreadsheetId=sheet_id, range=f"{sheet_name}!A1:A1")
            .execute()
        )
        if result.get("values"):
            return True  # headers already present
        service.spreadsheets().values().update(
            spreadsheetId=sheet_id,
            range=f"{sheet_name}!A1",
            valueInputOption="RAW",
            body={"values": [columns]},
        ).execute()
        print(f"[google_sheets] Header row written for '{sheet_name}'")
        return True
    except FileNotFoundError as exc:
        print(f"[google_sheets] Credentials file not found: {exc}", file=sys.stderr)
        return False
    except HttpError as exc:
        print(f"[google_sheets] ensure_headers API error {exc.status_code}: {exc.reason}", file=sys.stderr)
        return False
    except Exception as exc:
        print(f"[google_sheets] ensure_headers unexpected error: {exc}", file=sys.stderr)
        return False


def _get_sheet_tab_id(service, spreadsheet_id: str, sheet_name: str) -> Optional[int]:
    """Return the integer sheetId for a named tab (needed for batchUpdate)."""
    result = service.spreadsheets().get(spreadsheetId=spreadsheet_id).execute()
    for sheet in result.get("sheets", []):
        props = sheet.get("properties", {})
        if props.get("title") == sheet_name:
            return props["sheetId"]
    return None


def append_row_get_index(sheet_name: str, row: dict) -> Optional[int]:
    """Append a single row and return its 1-indexed row number, or None on failure.

    Identical to append_row but returns the row number so callers can apply
    per-cell formatting after writing.
    """
    try:
        columns = _column_names(sheet_name)
    except KeyError as exc:
        print(f"[google_sheets] Schema error: {exc}", file=sys.stderr)
        return None
    except (FileNotFoundError, yaml.YAMLError) as exc:
        print(f"[google_sheets] Failed to load sheet_schema.yaml: {exc}", file=sys.stderr)
        return None

    values = [
        "" if row.get(col) is None else str(row[col])
        for col in columns
    ]

    sheet_id = os.environ.get("GOOGLE_SHEET_ID")
    if not sheet_id:
        print("[google_sheets] GOOGLE_SHEET_ID is not set.", file=sys.stderr)
        return None
    if not os.environ.get("GOOGLE_APPLICATION_CREDENTIALS"):
        print("[google_sheets] GOOGLE_APPLICATION_CREDENTIALS is not set.", file=sys.stderr)
        return None

    try:
        service = _get_service()
        result = service.spreadsheets().values().append(
            spreadsheetId=sheet_id,
            range=f"{sheet_name}!A1",
            valueInputOption="RAW",
            insertDataOption="INSERT_ROWS",
            body={"values": [values]},
        ).execute()
        updated_range = result.get("updates", {}).get("updatedRange", "")
        # Parse row number from "TabName!A42:M42" → last segment "M42" → digits "42"
        row_part = updated_range.split("!")[-1].split(":")[-1]
        digits = "".join(c for c in row_part if c.isdigit())
        return int(digits) if digits else None
    except FileNotFoundError as exc:
        print(f"[google_sheets] Credentials file not found: {exc}", file=sys.stderr)
        return None
    except HttpError as exc:
        print(f"[google_sheets] Sheets API error {exc.status_code}: {exc.reason}", file=sys.stderr)
        return None
    except Exception as exc:
        print(f"[google_sheets] Unexpected error: {exc}", file=sys.stderr)
        return None


def _set_cell_background(
    sheet_name: str,
    row_1indexed: int,
    col_0indexed: int,
    red: float,
    green: float,
    blue: float,
) -> bool:
    """Set background color of a single cell via batchUpdate (shared helper)."""
    sheet_id = os.environ.get("GOOGLE_SHEET_ID")
    if not sheet_id or not os.environ.get("GOOGLE_APPLICATION_CREDENTIALS"):
        return False
    try:
        service = _get_service()
        tab_id = _get_sheet_tab_id(service, sheet_id, sheet_name)
        if tab_id is None:
            print(
                f"[google_sheets] Tab '{sheet_name}' not found — cannot set cell background",
                file=sys.stderr,
            )
            return False
        body = {
            "requests": [{
                "repeatCell": {
                    "range": {
                        "sheetId": tab_id,
                        "startRowIndex": row_1indexed - 1,
                        "endRowIndex": row_1indexed,
                        "startColumnIndex": col_0indexed,
                        "endColumnIndex": col_0indexed + 1,
                    },
                    "cell": {
                        "userEnteredFormat": {
                            "backgroundColor": {"red": red, "green": green, "blue": blue},
                        }
                    },
                    "fields": "userEnteredFormat.backgroundColor",
                }
            }]
        }
        service.spreadsheets().batchUpdate(spreadsheetId=sheet_id, body=body).execute()
        return True
    except HttpError as exc:
        print(
            f"[google_sheets] set_cell_background API error {exc.status_code}: {exc.reason}",
            file=sys.stderr,
        )
        return False
    except Exception as exc:
        print(f"[google_sheets] set_cell_background error: {exc}", file=sys.stderr)
        return False


def clear_cell_background(sheet_name: str, row_1indexed: int, col_0indexed: int) -> bool:
    """Reset a cell's background to white (overrides format inherited from row above).

    INSERT_ROWS appends carry the formatting from the preceding row. Call this
    immediately after append_row_get_index to prevent inherited yellow leaking
    into rows that should not be highlighted.
    """
    return _set_cell_background(sheet_name, row_1indexed, col_0indexed, 1.0, 1.0, 1.0)


def color_cell_yellow(sheet_name: str, row_1indexed: int, col_0indexed: int) -> bool:
    """Apply yellow background to a single cell.

    # 한 달 초과 게재 중인 광고로, 장기 운영/성과 추정 소재 검토 대상

    Args:
        sheet_name:    Tab name (must match a Sheets tab title).
        row_1indexed:  1-based row number (as returned by append_row_get_index).
        col_0indexed:  0-based column index (A=0, B=1, …).
    """
    return _set_cell_background(sheet_name, row_1indexed, col_0indexed, 1.0, 1.0, 0.0)


def read_sheet_rows(tab_name: str) -> list[dict]:
    """Read all data rows from a tab and return as list of header-keyed dicts.

    The first row is treated as the header. Returns [] on error or empty tab.
    """
    sheet_id = os.environ.get("GOOGLE_SHEET_ID")
    if not sheet_id or not os.environ.get("GOOGLE_APPLICATION_CREDENTIALS"):
        return []
    try:
        service = _get_service()
        result = (
            service.spreadsheets()
            .values()
            .get(spreadsheetId=sheet_id, range=f"{tab_name}!A:Z")
            .execute()
        )
        rows = result.get("values", [])
        if len(rows) < 2:
            return []
        header = rows[0]
        return [
            {header[i]: (row[i] if i < len(row) else "") for i in range(len(header))}
            for row in rows[1:]
        ]
    except HttpError as exc:
        print(
            f"[google_sheets] read_sheet_rows API error {exc.status_code}: {exc.reason}",
            file=sys.stderr,
        )
        return []
    except Exception as exc:
        print(f"[google_sheets] read_sheet_rows error: {exc}", file=sys.stderr)
        return []


def read_last_row(tab: str) -> Optional[list[Any]]:
    """Return the last row of a tab, or None if the tab is empty."""
    sheet_id = os.environ["GOOGLE_SHEET_ID"]
    service = _get_service()
    result = (
        service.spreadsheets()
        .values()
        .get(spreadsheetId=sheet_id, range=f"{tab}!A:Z")
        .execute()
    )
    values = result.get("values", [])
    return values[-1] if values else None
