"""Export crawling-workshop slides to PDF via Playwright screenshots.

Usage:
    python scripts/export_slides_pdf.py

Output: slides/crawling-workshop/crawling-workshop-slides.pdf
"""

import asyncio
import io
from pathlib import Path

from fpdf import FPDF
from PIL import Image
from playwright.async_api import async_playwright

_HTML_PATH = Path(__file__).parents[1] / "slides" / "crawling-workshop" / "index.html"
_PDF_PATH  = Path(__file__).parents[1] / "slides" / "crawling-workshop" / "crawling-workshop-slides.pdf"
_TOTAL     = 25
_W, _H     = 1280, 720  # px — 16:9


async def capture_slides() -> list[bytes]:
    screenshots = []
    async with async_playwright() as p:
        browser = await p.chromium.launch()
        page = await browser.new_page(viewport={"width": _W, "height": _H})

        file_url = f"file://{_HTML_PATH.resolve()}"
        await page.goto(file_url, wait_until="networkidle")
        await page.wait_for_timeout(1500)

        for i in range(1, _TOTAL + 1):
            slide_id = f"s{i:02d}"
            slide    = page.locator(f"#{slide_id}")

            await page.evaluate(
                f"document.getElementById('{slide_id}').scrollIntoView()"
            )
            await page.evaluate(
                f"document.getElementById('{slide_id}').classList.add('vis')"
            )
            await page.wait_for_timeout(800)

            shot = await slide.screenshot()
            screenshots.append(shot)
            print(f"  [{i:02d}/{_TOTAL}] captured {slide_id}")

        await browser.close()
    return screenshots


def build_pdf(screenshots: list[bytes]) -> None:
    # Page size in mm for 16:9 at ~96 dpi equivalent (254mm × 142.875mm)
    pw_mm = 254.0
    ph_mm = pw_mm * _H / _W  # ≈ 142.875

    pdf = FPDF(orientation="L", unit="mm", format=(ph_mm, pw_mm))
    pdf.set_auto_page_break(False)
    pdf.set_margins(0, 0, 0)

    for i, raw in enumerate(screenshots):
        img    = Image.open(io.BytesIO(raw))
        buf    = io.BytesIO()
        img.save(buf, format="PNG")
        buf.seek(0)

        pdf.add_page()
        pdf.image(buf, x=0, y=0, w=pw_mm, h=ph_mm)

    _PDF_PATH.parent.mkdir(parents=True, exist_ok=True)
    pdf.output(str(_PDF_PATH))


async def main():
    print(f"Exporting {_TOTAL} slides → {_PDF_PATH.name}")
    screenshots = await capture_slides()
    print(f"\nBuilding PDF...")
    build_pdf(screenshots)
    print(f"✓ Done: {_PDF_PATH}")


asyncio.run(main())
