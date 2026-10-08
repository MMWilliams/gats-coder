"""Bounded public web reading for documentation-aware Ledger prompts."""

from __future__ import annotations

from functools import lru_cache
from html import unescape
from html.parser import HTMLParser
import ipaddress
import re
import socket
import urllib.request
from urllib.parse import unquote, urlsplit


URL_RE = re.compile(r"https?://[^\s<>\])]+")


def _public_url(url: str) -> str:
    parts = urlsplit(url)
    if parts.scheme not in {"http", "https"} or not parts.hostname:
        raise ValueError("web URL must be http(s)")
    for result in socket.getaddrinfo(parts.hostname, None):
        address = ipaddress.ip_address(result[4][0])
        if not address.is_global:
            raise ValueError("web URL must resolve to a public address")
    return url


class _SafeRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, request, fp, code, msg, headers, newurl):
        _public_url(newurl)
        return super().redirect_request(request, fp, code, msg, headers, newurl)


class _Text(HTMLParser):
    def __init__(self):
        super().__init__()
        self.parts: list[str] = []
        self.skip = 0

    def handle_starttag(self, tag, attrs):
        if tag in {"script", "style", "nav"}:
            self.skip += 1
        if not self.skip and tag in {"p", "h1", "h2", "h3", "li", "dt", "dd", "pre"}:
            self.parts.append("\n")

    def handle_endtag(self, tag):
        if tag in {"script", "style", "nav"} and self.skip:
            self.skip -= 1

    def handle_data(self, data):
        if not self.skip:
            self.parts.append(data)


def _extract_html(html: str, fragment: str, max_chars: int) -> str:
    if fragment:
        escaped = re.escape(unquote(fragment))
        match = re.search(r'<[^>]+id=["\']' + escaped + r'["\'][^>]*>', html)
        if match:
            html = html[match.start():match.start() + max_chars * 4]
            end = html.find("</dd></dl>")
            if end > 0:
                html = html[:end + len("</dd></dl>")]
    else:
        match = re.search(r"<(?:main|article)\b", html, re.I)
        if match:
            html = html[match.start():]
    parser = _Text()
    parser.feed(html)
    text = unescape("".join(parser.parts))
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n\s*\n+", "\n", text).strip()
    return text[:max_chars]


@lru_cache(maxsize=64)
def read_page(url: str, max_chars: int = 3500) -> str:
    """Fetch public HTML/text, returning source-labelled, size-limited content."""
    _public_url(url)
    request = urllib.request.Request(url, headers={"User-Agent": "Ledger/0.14 documentation reader"})
    opener = urllib.request.build_opener(_SafeRedirect())
    with opener.open(request, timeout=20) as response:
        content_type = response.headers.get("Content-Type", "").lower()
        if not any(kind in content_type for kind in ("text/html", "text/plain")):
            raise ValueError("web page is not HTML or plain text")
        raw = response.read(1_000_001)
        if len(raw) > 1_000_000:
            raise ValueError("web page exceeds 1 MB limit")
        charset = response.headers.get_content_charset() or "utf-8"
    body = raw.decode(charset, errors="replace")
    content = (_extract_html(body, urlsplit(url).fragment, max_chars)
               if "html" in content_type else body[:max_chars])
    return f"SOURCE: {url}\n{content}"


def web_context(text: str, max_pages: int = 6, max_chars: int = 3500,
                query: str = "") -> str:
    """Read linked docs before authoring; one failed page never blocks a patch."""
    urls = list(dict.fromkeys(match.group(0).rstrip(".,;'")
                              for match in URL_RE.finditer(text)))
    # Specific API anchors are more useful than broad documentation indexes.
    terms = set(re.findall(r"[a-z][a-z_]{4,}", query.lower()))
    urls.sort(key=lambda url: (-sum(term in url.lower() for term in terms),
                               not bool(urlsplit(url).fragment), url))
    pages = []
    for url in urls[:max_pages]:
        try:
            pages.append(read_page(url, max_chars))
        except (OSError, ValueError, UnicodeError) as exc:
            pages.append(f"SOURCE: {url}\nUnavailable: {exc}")
    return "\n\n".join(pages)
