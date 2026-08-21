"""Look things up, because documentation changes.

A model's training has a cutoff; the flag it remembers may have been renamed
and the endpoint it remembers may be gone. When the agent has to work out
HOW to do something it has never been told how to do, guessing from memory
is the failure mode -- so it reads first.

stdlib only, like the rest of the package: urllib and a small amount of tag
stripping. A search API key is used when one is configured (`ledger setup
--key brave`), and the keyless HTML endpoint is the fallback.

Everything fetched is returned as text with its source URL attached, so
what the agent read is in the journal next to what it then did. A citation
that cannot be checked is not evidence.
"""

from __future__ import annotations

import gzip
import html
import json
import re
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass

UA = ("Mozilla/5.0 (compatible; ledger-agent/1.0; "
      "+https://kyberos.ai/ledger)")

_TAG = re.compile(r"<(script|style)[^>]*>.*?</\1>", re.S | re.I)
_ANY_TAG = re.compile(r"<[^>]+>")
_WS = re.compile(r"[ \t\r\f\v]+")
_BLANK = re.compile(r"\n{3,}")


@dataclass(frozen=True)
class Finding:
    title: str
    url: str
    snippet: str

    def cite(self) -> str:
        return f"{self.title} <{self.url}>"


def _get(url: str, timeout: int = 25, headers: dict | None = None) -> str:
    req = urllib.request.Request(
        url, headers={"user-agent": UA, "accept-encoding": "gzip",
                      **(headers or {})})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        raw = resp.read()
        if resp.headers.get("content-encoding") == "gzip":
            raw = gzip.decompress(raw)
        return raw.decode("utf-8", errors="replace")


def to_text(markup: str, limit: int = 20000) -> str:
    """HTML to something a model can read, without a parser dependency."""
    text = _TAG.sub(" ", markup)
    text = re.sub(r"<br\s*/?>|</p>|</div>|</li>|</h[1-6]>", "\n", text,
                  flags=re.I)
    text = _ANY_TAG.sub(" ", text)
    text = html.unescape(text)
    text = _WS.sub(" ", text)
    text = "\n".join(line.strip() for line in text.splitlines())
    text = _BLANK.sub("\n\n", text).strip()
    return text[:limit]


def _brave(query: str, key: str, count: int) -> list[Finding]:
    url = ("https://api.search.brave.com/res/v1/web/search?"
           + urllib.parse.urlencode({"q": query, "count": count}))
    body = _get(url, headers={"x-subscription-token": key,
                              "accept": "application/json"})
    data = json.loads(body)
    out = []
    for hit in (data.get("web", {}).get("results") or [])[:count]:
        out.append(Finding(title=hit.get("title", ""),
                           url=hit.get("url", ""),
                           snippet=to_text(hit.get("description", ""), 400)))
    return out


_DDG_HIT = re.compile(
    r'<a rel="nofollow" class="result__a" href="(?P<url>[^"]+)"[^>]*>'
    r"(?P<title>.*?)</a>.*?"
    r'class="result__snippet"[^>]*>(?P<snip>.*?)</a>', re.S)


def _duckduckgo(query: str, count: int) -> list[Finding]:
    """Keyless fallback. Scraping is brittle by nature, so a change in
    their markup must read as 'no results', never as a crash mid-episode."""
    body = _get("https://html.duckduckgo.com/html/?"
                + urllib.parse.urlencode({"q": query}))
    out = []
    for m in _DDG_HIT.finditer(body):
        url = html.unescape(m.group("url"))
        # their links are wrapped in a redirect carrying the real target
        parsed = urllib.parse.urlparse(url)
        if parsed.path.startswith("/l/"):
            qs = urllib.parse.parse_qs(parsed.query)
            url = (qs.get("uddg") or [url])[0]
        out.append(Finding(title=to_text(m.group("title"), 200),
                           url=url,
                           snippet=to_text(m.group("snip"), 400)))
        if len(out) >= count:
            break
    return out


def search(query: str, count: int = 5) -> list[Finding]:
    """Web results, or an empty list. Never raises: a search that fails is
    a fact about the network, not a reason to abandon the episode."""
    from .credentials import get as key_for
    try:
        key = key_for("brave")
        if key:
            return _brave(query, key, count)
        return _duckduckgo(query, count)
    except (urllib.error.URLError, urllib.error.HTTPError, OSError,
            ValueError, json.JSONDecodeError):
        return []


def read(url: str, limit: int = 20000) -> str:
    """A page as text, or "" if it cannot be had."""
    try:
        return to_text(_get(url), limit=limit)
    except (urllib.error.URLError, urllib.error.HTTPError, OSError):
        return ""


def brief(query: str, pages: int = 2, per_page: int = 6000) -> tuple[str, list[str]]:
    """Search, read the top hits, and return (text, sources).

    The sources come back separately so the caller can journal exactly what
    was read. An agent that cites nothing is indistinguishable from one that
    made it up.
    """
    findings = search(query)
    if not findings:
        return "", []
    chunks, sources = [], []
    for finding in findings[:pages]:
        body = read(finding.url, limit=per_page)
        if not body:
            continue
        sources.append(finding.url)
        chunks.append(f"### {finding.title}\n{finding.url}\n\n{body}")
    if not chunks:
        # Even with no page bodies, the snippets are worth something.
        chunks = [f"### {f.title}\n{f.url}\n{f.snippet}" for f in findings]
        sources = [f.url for f in findings]
    return "\n\n".join(chunks), sources
