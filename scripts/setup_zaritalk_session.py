#!/usr/bin/env python3
"""Setup Zaritalk browser session for automated supply collection.

Opens Zaritalk in a visible (non-headless) browser. Log in manually, confirm
the room-count button text (e.g. '이 지역 방 953개 보기') is visible, then
press Enter to save the session.

The saved session is stored at .auth/zaritalk_state.json and loaded
automatically by src/collectors/supply/zaristay.py on every daily run.

Usage:
    source .venv/bin/activate
    python scripts/setup_zaritalk_session.py

Re-run this script if the session expires (zaristay collector returns
status=login_required again).
"""

import asyncio
from pathlib import Path

from playwright.async_api import async_playwright

_REPO_ROOT = Path(__file__).parent.parent
_AUTH_DIR = _REPO_ROOT / ".auth"
_STATE_FILE = _AUTH_DIR / "zaritalk_state.json"
_TARGET_URL = (
    "https://tenant.zaritalk.com/short-term-vacancy"
    "?query=%EC%84%9C%EC%9A%B8%ED%8A%B9%EB%B3%84%EC%8B%9C"
)


async def main() -> None:
    _AUTH_DIR.mkdir(parents=True, exist_ok=True)

    print("=" * 60)
    print("Zaritalk session setup")
    print("=" * 60)
    print(f"\nTarget URL:\n  {_TARGET_URL}\n")
    print("Steps:")
    print("  1. A browser window will open and navigate to Zaritalk.")
    print("  2. Log in with your Zaritalk credentials.")
    print("  3. Confirm the Seoul page shows '이 지역 방 N개 보기' button.")
    print("  4. Return here and press Enter to save the session.")
    print(f"\nSession will be saved to:\n  {_STATE_FILE}\n")

    async with async_playwright() as p:
        browser = await p.chromium.launch(
            headless=False,
            args=["--start-maximized"],
        )
        context = await browser.new_context(
            no_viewport=True,
            locale="ko-KR",
        )
        page = await context.new_page()
        await page.goto(_TARGET_URL)

        print("Browser is open. Log in, then press Enter here when ready.")
        input("Press Enter to save session... ")

        await context.storage_state(path=str(_STATE_FILE))
        print(f"\nSession saved to: {_STATE_FILE}")
        print("You can now run: python -m src.jobs.collect_supply")

        await browser.close()


if __name__ == "__main__":
    asyncio.run(main())
