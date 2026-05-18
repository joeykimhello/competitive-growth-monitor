"""Google Chat alert client.

Sends messages to a Google Chat space via an incoming webhook URL read from
GOOGLE_CHAT_WEBHOOK_URL. Each webhook is bound to a specific space, so no
channel parameter is needed.
"""

import os
import sys

import requests
from dotenv import load_dotenv

load_dotenv()


def send_google_chat_message(text: str) -> bool:
    """Send a plain-text message to the configured Google Chat space.

    Reads GOOGLE_CHAT_WEBHOOK_URL from the environment. Returns True on
    success, False on any failure. Errors are printed to stderr so callers
    can decide whether to raise or continue.

    Args:
        text: Message body. Supports basic Google Chat markdown (*bold*, _italic_).

    Returns:
        True if the message was delivered (HTTP 2xx), False otherwise.
    """
    webhook_url = os.environ.get("GOOGLE_CHAT_WEBHOOK_URL")
    if not webhook_url:
        print(
            "[google_chat] GOOGLE_CHAT_WEBHOOK_URL is not set.",
            file=sys.stderr,
        )
        return False

    try:
        response = requests.post(
            webhook_url,
            json={"text": text},
            timeout=10,
        )
        response.raise_for_status()
        return True
    except requests.exceptions.Timeout:
        print("[google_chat] Request timed out.", file=sys.stderr)
        return False
    except requests.exceptions.ConnectionError as exc:
        print(f"[google_chat] Connection error: {exc}", file=sys.stderr)
        return False
    except requests.exceptions.HTTPError as exc:
        print(
            f"[google_chat] HTTP {exc.response.status_code}: {exc.response.text}",
            file=sys.stderr,
        )
        return False
    except requests.exceptions.RequestException as exc:
        print(f"[google_chat] Unexpected error: {exc}", file=sys.stderr)
        return False


def send_policy_change_alert(competitor: str, policy_page: str, url: str, diff_summary: str) -> bool:
    """Send a structured policy change alert to Google Chat."""
    text = (
        f"*Policy change detected*\n"
        f"*Competitor:* {competitor}\n"
        f"*Page:* {policy_page}\n"
        f"*URL:* {url}\n"
        f"*Summary:* {diff_summary}"
    )
    return send_google_chat_message(text)
