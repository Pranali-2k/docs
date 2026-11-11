"""Turn email bodies (often Outlook/Word HTML with <style> blocks, MSO markup and entities) into readable text.

html_to_text() keeps the structure people see in their mail client (paragraphs, line breaks, bullets, links)
and drops everything that isn't content: <head>, <style>, <script>, comments, conditional MSO blocks, images.
clean_body() is the single entry point used at intake; it only converts when the body actually looks like HTML.
Neither function ever raises: on any parser problem they fall back to a regex strip, then to the input.
"""
from __future__ import annotations

import html
import logging
import re
from html.parser import HTMLParser

log = logging.getLogger("docshealth.text")

_HTML_HINT = re.compile(r"<\s*(html|body|div|p|br|span|table|style|head|meta|font|li|ul|ol|td|tr|o:p)\b[^>]*>", re.I)
_SKIP = {"style", "script", "head", "title", "xml", "noscript", "template", "o:p"}
_BLOCK = {"p", "div", "section", "article", "header", "footer", "blockquote", "pre", "table", "tr",
          "h1", "h2", "h3", "h4", "h5", "h6", "ul", "ol", "hr", "address", "center"}


class _Extractor(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.out: list[str] = []
        self.skip_depth = 0
        self.href: str | None = None
        self.link_text: list[str] = []

    def _nl(self, n: int = 1) -> None:
        self.out.append("\n" * n)

    def handle_starttag(self, tag, attrs):
        tag = tag.lower()
        if tag in _SKIP:
            if tag != "o:p":
                self.skip_depth += 1
            return
        if self.skip_depth:
            return
        if tag == "br":
            self._nl()
        elif tag == "li":
            self._nl()
            self.out.append("• ")
        elif tag in ("td", "th"):
            self.out.append("  ")
        elif tag in _BLOCK:
            self._nl()
        elif tag == "a":
            a = dict(attrs)
            # Outlook "safe links" wrap the real URL; prefer the original when present
            self.href = a.get("originalsrc") or a.get("href")
            self.link_text = []

    def handle_startendtag(self, tag, attrs):
        if tag.lower() == "br" and not self.skip_depth:
            self._nl()
        elif tag.lower() == "hr" and not self.skip_depth:
            self._nl()

    def handle_endtag(self, tag):
        tag = tag.lower()
        if tag in _SKIP:
            if tag != "o:p":
                self.skip_depth = max(0, self.skip_depth - 1)
            return
        if self.skip_depth:
            return
        if tag == "a":
            text = "".join(self.link_text).strip()
            href = (self.href or "").strip()
            if href and not href.lower().startswith(("mailto:", "#", "cid:")) and href not in text:
                self.out.append(f" ({href})")
            self.href = None
        elif tag in _BLOCK or tag == "li":
            self._nl()

    def handle_data(self, data):
        if self.skip_depth:
            return
        self.out.append(data)
        if self.href is not None:
            self.link_text.append(data)


def _tidy(text: str) -> str:
    text = text.replace("\r\n", "\n").replace("\r", "\n").replace("\xa0", " ").replace("​", "")
    lines = [re.sub(r"[ \t\f\v]+", " ", line).strip() for line in text.split("\n")]
    text = "\n".join(lines)
    return re.sub(r"\n{3,}", "\n\n", text).strip()


def _regex_strip(raw: str) -> str:
    s = re.sub(r"(?is)<!--.*?-->", " ", raw)
    s = re.sub(r"(?is)<(style|script|head|title|xml)\b.*?</\1\s*>", " ", s)
    s = re.sub(r"(?i)<br\s*/?>|</(p|div|li|tr|h[1-6])\s*>", "\n", s)
    s = re.sub(r"<[^>]+>", " ", s)
    return _tidy(html.unescape(s))


def looks_like_html(text: str | None) -> bool:
    return bool(text) and bool(_HTML_HINT.search(text[:20000]))


def html_to_text(raw: str) -> str:
    try:
        raw = re.sub(r"(?is)<!--.*?-->", " ", raw)  # also removes MSO conditional comments and CSS in comments
        p = _Extractor()
        p.feed(raw)
        p.close()
        return _tidy("".join(p.out))
    except Exception:  # malformed markup: fall back to a plain strip rather than storing HTML
        log.warning("HTML parser failed; using regex strip", exc_info=True)
        try:
            return _regex_strip(raw)
        except Exception:
            return raw


def clean_body(body: str | None) -> str:
    """Readable text for storage/AI. Plain text passes through (whitespace tidied); HTML is converted."""
    if not body:
        return ""
    try:
        return html_to_text(body) if looks_like_html(body) else _tidy(body)
    except Exception:
        return body
