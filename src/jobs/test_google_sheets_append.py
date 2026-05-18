"""CLI test script for the Google Sheets append integration.

Appends one sample row to the policy_change_log tab using append_row so the
full path — env vars → schema loading → Sheets API — is exercised end to end.

Usage:
    python -m src.jobs.test_google_sheets_append

Exits 0 on success, 1 on failure.
"""

import sys
from datetime import datetime, timezone

from src.integrations.google_sheets import append_row

_SHEET = "policy_change_log"

_SAMPLE_ROW = {
    "detected_at": datetime.now(timezone.utc).isoformat(),
    "env": "dev",
    "competitor": "airbnb",
    "policy_page": "cancellation_policy",
    "url": "https://www.airbnb.com/help/article/149",
    "previous_hash": "",
    "current_hash": "test-hash-abc123",
    "diff_summary": "Test row — Google Sheets append integration test",
    "alert_sent": "False",
}


def main() -> None:
    print(f"Appending sample row to sheet tab '{_SHEET}'...")
    for key, value in _SAMPLE_ROW.items():
        print(f"  {key}: {value}")

    success = append_row(_SHEET, _SAMPLE_ROW)

    if success:
        print("\nOK — row appended successfully.")
        sys.exit(0)
    else:
        print("\nFAILED — see error above.", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
