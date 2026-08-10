"""Build ledger-essay.pdf (via Edge headless) and ledger-essay.epub
(hand-rolled EPUB3 + NCX, Send-to-Kindle's preferred reflowable format)
from essay_master.html."""

import re
import subprocess
import sys
import uuid
import zipfile
from datetime import datetime, timezone
from pathlib import Path

HERE = Path(__file__).parent
MASTER_PATH = Path(sys.argv[1]) if len(sys.argv) > 1 \
    else HERE / "essay_master.html"
OUT_BASE = sys.argv[2] if len(sys.argv) > 2 else "ledger-essay"
KINDLE = len(sys.argv) > 3 and sys.argv[3] == "kindle"
MASTER = MASTER_PATH.read_text(encoding="utf-8")
EDGE = r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe"

meta_block = re.search(r"<!-- BOOK\n(.*?)\n-->", MASTER, re.DOTALL).group(1)
META = dict(line.split(": ", 1) for line in meta_block.strip().splitlines())
CHAPTERS = re.findall(
    r'<section data-title="(.*?)">\n(.*?)\n</section>', MASTER, re.DOTALL)
assert CHAPTERS, "no chapters parsed"

# ---------------------------------------------------------------- PDF ------
# Kindle mode: page geometry sized for a ~7" e-reader screen so the PDF is
# readable without zooming or panning; one text column, generous line height.
KINDLE_CSS = """
@page { size: 91mm 122mm; margin: 6mm 7mm; }
body { font-family: Georgia, 'Times New Roman', serif; font-size: 8.5pt;
       line-height: 1.45; color: #111; margin: 0; }
h1 { font-size: 13pt; line-height: 1.2; margin: 0 0 0.7em 0;
     page-break-before: always; padding-top: 0.4em; }
.titlepage { text-align: center; padding-top: 26mm; page-break-after: always; }
.titlepage h1 { page-break-before: avoid; font-size: 17pt; padding-top: 0; }
.titlepage .sub { font-size: 9.5pt; font-style: italic; color: #333;
                  margin-top: 1em; }
.titlepage .auth { margin-top: 2.5em; font-size: 8.5pt; }
p { margin: 0 0 0.7em 0; text-align: justify; }
ul { margin: 0 0 0.8em 1em; padding: 0; }
li { margin-bottom: 0.4em; }
code { font-family: Consolas, monospace; font-size: 0.85em; }
pre { font-family: Consolas, monospace; font-size: 7pt;
      padding: 0.5em 0.6em; margin: 0 0 0.8em 0; background: #f3f3ef;
      page-break-inside: avoid; white-space: pre-wrap; }
"""

PDF_CSS = """
@page { margin: 22mm 19mm; }
body { font-family: Georgia, 'Times New Roman', serif; font-size: 11.5pt;
       line-height: 1.55; color: #1a1a1a; max-width: 42em; margin: 0 auto; }
h1 { font-size: 19pt; line-height: 1.25; margin: 0 0 0.9em 0;
     page-break-before: always; padding-top: 1.5em; }
.titlepage { text-align: center; padding-top: 34mm; page-break-after: always; }
.titlepage h1 { page-break-before: avoid; font-size: 30pt; padding-top: 0; }
.titlepage .sub { font-size: 14pt; font-style: italic; color: #444;
                  margin-top: 1.2em; }
.titlepage .auth { margin-top: 3.5em; font-size: 12pt; }
p { margin: 0 0 0.85em 0; text-align: justify; }
ul { margin: 0 0 0.9em 1.2em; }
li { margin-bottom: 0.45em; }
code { font-family: Consolas, monospace; font-size: 0.88em;
       background: #f2f2ee; padding: 0 0.18em; }
pre { font-family: Consolas, monospace; font-size: 9.5pt; background: #f2f2ee;
      padding: 0.8em 1em; margin: 0 0 0.9em 0; page-break-inside: avoid; }
strong { letter-spacing: 0.01em; }
"""

pdf_body = [
    '<div class="titlepage">',
    f'<h1>{META["title"]}</h1>',
    f'<div class="sub">{META["subtitle"]}</div>',
    f'<div class="auth">{META["author"]}</div>',
    "</div>",
]
for _, body in CHAPTERS:
    pdf_body.append(body)

css = KINDLE_CSS if KINDLE else PDF_CSS
pdf_html = (f'<!DOCTYPE html><html lang="{META["lang"]}"><head>'
            f'<meta charset="utf-8"><title>{META["title"]}</title>'
            f"<style>{css}</style></head><body>"
            + "\n".join(pdf_body) + "</body></html>")
pdf_src = HERE / f"_pdf_render_{OUT_BASE}.html"
pdf_src.write_text(pdf_html, encoding="utf-8")
pdf_out = HERE / (f"{OUT_BASE}-kindle.pdf" if KINDLE else f"{OUT_BASE}.pdf")
subprocess.run(
    [EDGE, "--headless=new", "--disable-gpu", "--no-first-run",
     f"--print-to-pdf={pdf_out}", "--no-pdf-header-footer",
     pdf_src.resolve().as_uri()],
    check=True, timeout=120, capture_output=True)
assert pdf_out.stat().st_size > 20_000, "PDF suspiciously small"
print(f"PDF : {pdf_out} ({pdf_out.stat().st_size:,} bytes)")
if KINDLE:
    sys.exit(0)  # kindle mode builds the PDF only

# --------------------------------------------------------------- EPUB ------
EPUB_CSS = """
body { font-family: serif; line-height: 1.5; }
h1 { font-size: 1.5em; line-height: 1.3; margin: 1em 0; }
p { margin: 0 0 0.7em 0; }
ul { margin: 0 0 0.8em 1.1em; }
li { margin-bottom: 0.4em; }
code { font-family: monospace; font-size: 0.9em; }
pre { font-family: monospace; font-size: 0.82em; margin: 0.8em 0;
      white-space: pre-wrap; }
.titlepage { text-align: center; margin-top: 18%; }
.titlepage h1 { font-size: 2em; }
.sub { font-style: italic; margin-top: 1em; }
.auth { margin-top: 3em; }
"""


def xhtml(title: str, body: str) -> str:
    return ('<?xml version="1.0" encoding="utf-8"?>\n<!DOCTYPE html>\n'
            '<html xmlns="http://www.w3.org/1999/xhtml" '
            f'xml:lang="{META["lang"]}"><head><title>{title}</title>'
            '<link rel="stylesheet" type="text/css" href="style.css"/>'
            f"</head><body>{body}</body></html>")


book_id = f"urn:uuid:{uuid.uuid4()}"
modified = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

chapter_files = [(f"ch{i:02d}.xhtml", title, body)
                 for i, (title, body) in enumerate(CHAPTERS, 1)]

manifest, spine, nav_lis, ncx_points = [], [], [], []
manifest.append('<item id="style" href="style.css" media-type="text/css"/>')
manifest.append('<item id="nav" href="nav.xhtml" properties="nav" '
                'media-type="application/xhtml+xml"/>')
manifest.append('<item id="ncx" href="toc.ncx" '
                'media-type="application/x-dtbncx+xml"/>')
manifest.append('<item id="titlepage" href="titlepage.xhtml" '
                'media-type="application/xhtml+xml"/>')
spine.append('<itemref idref="titlepage"/>')
for i, (fname, title, _) in enumerate(chapter_files, 1):
    manifest.append(f'<item id="c{i}" href="{fname}" '
                    'media-type="application/xhtml+xml"/>')
    spine.append(f'<itemref idref="c{i}"/>')
    nav_lis.append(f'<li><a href="{fname}">{title}</a></li>')
    ncx_points.append(
        f'<navPoint id="np{i}" playOrder="{i}"><navLabel>'
        f"<text>{title}</text></navLabel>"
        f'<content src="{fname}"/></navPoint>')

opf = f'''<?xml version="1.0" encoding="utf-8"?>
<package xmlns="http://www.idpf.org/2007/opf" version="3.0"
         unique-identifier="bookid">
<metadata xmlns:dc="http://purl.org/dc/elements/1.1/">
<dc:identifier id="bookid">{book_id}</dc:identifier>
<dc:title>{META["title"]}: {META["subtitle"]}</dc:title>
<dc:creator>{META["author"]}</dc:creator>
<dc:language>{META["lang"]}</dc:language>
<meta property="dcterms:modified">{modified}</meta>
</metadata>
<manifest>{"".join(manifest)}</manifest>
<spine toc="ncx">{"".join(spine)}</spine>
</package>'''

nav = xhtml("Contents",
            '<nav epub:type="toc" xmlns:epub="http://www.idpf.org/2007/ops">'
            f'<h1>Contents</h1><ol>{"".join(nav_lis)}</ol></nav>')

ncx = f'''<?xml version="1.0" encoding="utf-8"?>
<ncx xmlns="http://www.daisy.org/z3986/2005/ncx/" version="2005-1">
<head><meta name="dtb:uid" content="{book_id}"/></head>
<docTitle><text>{META["title"]}</text></docTitle>
<navMap>{"".join(ncx_points)}</navMap></ncx>'''

titlepage = xhtml(META["title"],
                  f'<div class="titlepage"><h1>{META["title"]}</h1>'
                  f'<div class="sub">{META["subtitle"]}</div>'
                  f'<div class="auth">{META["author"]}</div></div>')

epub_out = HERE / f"{OUT_BASE}.epub"
with zipfile.ZipFile(epub_out, "w", zipfile.ZIP_DEFLATED, compresslevel=9) as z:
    z.writestr(zipfile.ZipInfo("mimetype"), "application/epub+zip",
               compress_type=zipfile.ZIP_STORED)
    z.writestr("META-INF/container.xml",
               '<?xml version="1.0" encoding="utf-8"?>'
               '<container version="1.0" xmlns="urn:oasis:names:tc:'
               'opendocument:xmlns:container"><rootfiles>'
               '<rootfile full-path="OEBPS/content.opf" '
               'media-type="application/oebps-package+xml"/>'
               "</rootfiles></container>")
    z.writestr("OEBPS/content.opf", opf)
    z.writestr("OEBPS/nav.xhtml", nav)
    z.writestr("OEBPS/toc.ncx", ncx)
    z.writestr("OEBPS/style.css", EPUB_CSS)
    z.writestr("OEBPS/titlepage.xhtml", titlepage)
    for fname, title, body in chapter_files:
        z.writestr(f"OEBPS/{fname}", xhtml(title, body))
print(f"EPUB: {epub_out} ({epub_out.stat().st_size:,} bytes)")

# sanity: chapters parse as XML
import xml.etree.ElementTree as ET
for fname, title, body in chapter_files:
    ET.fromstring(xhtml(title, body))
ET.fromstring(nav)
ET.fromstring(opf)
ET.fromstring(ncx)
print(f"XML validation OK ({len(chapter_files)} chapters)")
words = sum(len(re.sub(r"<[^>]+>", " ", b).split()) for _, b in CHAPTERS)
print(f"~{words:,} words")
