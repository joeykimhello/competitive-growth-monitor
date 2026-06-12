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


_CHAT_LIMIT = 4000  # Google Chat webhook hard limit is 4096; use 4000 for safety


def _split_message(text: str, limit: int = _CHAT_LIMIT) -> list[str]:
    """Split text into chunks that fit within limit, breaking at newlines."""
    if len(text) <= limit:
        return [text]
    chunks = []
    current = []
    current_len = 0
    for line in text.splitlines(keepends=True):
        if current_len + len(line) > limit:
            if current:
                chunks.append("".join(current))
            current = [line]
            current_len = len(line)
        else:
            current.append(line)
            current_len += len(line)
    if current:
        chunks.append("".join(current))
    return chunks


def _post_to_webhook(webhook_url: str, text: str) -> bool:
    try:
        response = requests.post(webhook_url, json={"text": text}, timeout=10)
        response.raise_for_status()
        return True
    except requests.exceptions.Timeout:
        print("[google_chat] Request timed out.", file=sys.stderr)
    except requests.exceptions.ConnectionError as exc:
        print(f"[google_chat] Connection error: {exc}", file=sys.stderr)
    except requests.exceptions.HTTPError as exc:
        print(f"[google_chat] HTTP {exc.response.status_code}: {exc.response.text}", file=sys.stderr)
    except requests.exceptions.RequestException as exc:
        print(f"[google_chat] Unexpected error: {exc}", file=sys.stderr)
    return False


def send_google_chat_message(text: str) -> bool:
    """Send a plain-text message to the configured Google Chat space.

    Automatically splits messages that exceed the 4096-character limit into
    multiple sequential messages. Returns True only if all parts succeed.
    """
    webhook_url = os.environ.get("GOOGLE_CHAT_WEBHOOK_URL")
    if not webhook_url:
        print("[google_chat] GOOGLE_CHAT_WEBHOOK_URL is not set.", file=sys.stderr)
        return False

    chunks = _split_message(text)
    if len(chunks) > 1:
        print(f"[google_chat] Message split into {len(chunks)} parts ({len(text)} chars total)")

    return all(_post_to_webhook(webhook_url, chunk) for chunk in chunks)


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
