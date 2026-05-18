#!/usr/bin/env python3
"""Setup AirDNA browser session for automated supply collection.

Opens AirDNA in a visible (non-headless) browser. Log in manually, confirm
the listings page is showing Total Active Listings, then press Enter to save
the session.

The saved session is stored at .auth/airdna_state.json and loaded
automatically by src/collectors/supply/airbnb.py on every daily run.

Usage:
    source .venv/bin/activate
    python scripts/setup_airdna_session.py

Re-run this script if the session expires (airbnb collector returns
status=login_required again).
"""

import asyncio
from pathlib import Path

from playwright.async_api import async_playwright

_REPO_ROOT = Path(__file__).parent.parent
_AUTH_DIR = _REPO_ROOT / ".auth"
_STATE_FILE = _AUTH_DIR / "airdna_state.json"
_TARGET_URL = (
    "https://app.airdna.co/data/kr/45/listings"
    "?lat=37.564679&lng=126.97451&zoom=11.08"
)


async def main() -> None:
    _AUTH_DIR.mkdir(parents=True, exist_ok=True)

    print("=" * 60)
    print("AirDNA session setup")
    print("=" * 60)
    print(f"\nTarget URL:\n  {_TARGET_URL}\n")
    print("Steps:")
    print("  1. A browser window will open and navigate to AirDNA.")
    print("  2. Log in with your AirDNA credentials.")
    print("  3. Confirm the Seoul listings page shows 'Total Active Listings'.")
    print("  4. Return here and press Enter to save the session.")
    print(f"\nSession will be saved to:\n  {_STATE_FILE}\n")

    async with async_playwright() as p:
        browser = await p.chromium.launch(
            headless=False,
            args=["--start-maximized"],
        )
        context = await browser.new_context(
            no_viewport=True,
            locale="en-US",
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
