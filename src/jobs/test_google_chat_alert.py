"""CLI test script for the Google Chat webhook integration.

Usage:
    python -m src.jobs.test_google_chat_alert

Sends a test message to the Google Chat space configured in GOOGLE_CHAT_WEBHOOK_URL.
Exits 0 on success, 1 on failure.
"""

import sys

from src.integrations.google_chat import send_google_chat_message

_TEST_MESSAGE = "Competitive Growth Monitor test alert"


def main() -> None:
    print(f'Sending test message: "{_TEST_MESSAGE}"')
    success = send_google_chat_message(_TEST_MESSAGE)
    if success:
        print("OK — message delivered.")
        sys.exit(0)
    else:
        print("FAILED — see error above.", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
