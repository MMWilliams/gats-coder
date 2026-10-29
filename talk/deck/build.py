"""Compile the deck: one PNG per slide, a contact sheet, and a PDF.

    python build.py            # index.html -> talk/deck/out/
    python build.py alt.html   # alt.html   -> talk/deck/out/alt/
"""
import sys
from pathlib import Path

from playwright.sync_api import sync_playwright

import script

HERE = Path(__file__).parent
DECK = sys.argv[1] if len(sys.argv) > 1 else "index.html"
OUT = HERE / "out" if DECK == "index.html" else HERE / "out" / Path(DECK).stem


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    url = (HERE / DECK).resolve().as_uri()
    with sync_playwright() as p:
        browser = p.chromium.launch(channel="chrome")
        # reduced motion: renders each slide in its final, fully animated state
        page = browser.new_page(viewport={"width": 1600, "height": 900}, reduced_motion="reduce")
        page.goto(url)
        count = page.locator(".slide").count()
        for i in range(count):
            page.goto(f"{url}?all#{i + 1}")  # every step shown
            page.reload()
            page.wait_for_timeout(1400)  # let bars and fades settle
            page.screenshot(path=str(OUT / f"slide-{i + 1:02d}.png"))
        page.emulate_media(media="print")
        page.pdf(path=str(OUT / "deck.pdf"), width="1600px", height="900px",
                 print_background=True)
        sheet = browser.new_page(viewport={"width": 1600, "height": 900})
        tiles = "".join(f'<img src="slide-{i + 1:02d}.png">' for i in range(count))
        (OUT / "sheet.html").write_text(
            "<style>body{margin:0;background:#ccc;display:grid;"
            "grid-template-columns:repeat(4,1fr);gap:8px;padding:8px}"
            "img{width:100%;display:block}</style>" + tiles, encoding="utf-8")
        sheet.goto((OUT / "sheet.html").resolve().as_uri())
        sheet.wait_for_timeout(500)
        sheet.screenshot(path=str(OUT / "contact-sheet.png"), full_page=True)
        browser.close()
    print(f"{count} slides -> {OUT}")
    if DECK == "index.html":
        script.export()


if __name__ == "__main__":
    main()
