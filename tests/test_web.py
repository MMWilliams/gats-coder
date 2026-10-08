"""Web documentation is bounded, targeted, and safe to include in prompts."""

import pytest

from ledger import web


def test_anchor_extracts_api_entry_instead_of_navigation():
    html = ('<nav>Noise</nav><main><p>Intro</p>'
            '<dl><dt id="bpy.types.Mesh.from_pydata">from_pydata(vertices, edges, faces)</dt>'
            '<dd><p>Make a mesh from vertices, edges, and faces.</p></dd></dl>'
            '<p>Unrelated</p></main>')
    text = web._extract_html(html, "bpy.types.Mesh.from_pydata", 500)
    assert "from_pydata(vertices, edges, faces)" in text
    assert "Make a mesh" in text
    assert "Noise" not in text
    assert "Unrelated" not in text


def test_web_context_prioritizes_error_property(monkeypatch):
    seen = []
    monkeypatch.setattr(web, "read_page", lambda url, chars: seen.append(url) or url)
    links = ("https://docs.blender.org/api/current/index.html "
             "https://docs.blender.org/api/current/bpy.types.ImageFormatSettings.html"
             "#bpy.types.ImageFormatSettings.color_depth")
    web.web_context(links, max_pages=1, query="color_depth expected a string enum")
    assert seen[0].endswith("#bpy.types.ImageFormatSettings.color_depth")


def test_web_reader_rejects_local_address():
    with pytest.raises(ValueError):
        web._public_url("http://127.0.0.1/private")
