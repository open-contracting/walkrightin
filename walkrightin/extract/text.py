"""Find Drive URLs and short links in text, tolerating markup, code and broken syntax."""

import html
import re
from dataclasses import dataclass
from urllib.parse import unquote

from walkrightin.urls import DRIVE, WRAPPERS, clean, drive_id, normalize_short, strip, unwrap

MD_LINK = re.compile(r"\[([^\]]+)\]\(\s*<?([^\s)>]+)")
HTML_LINK = re.compile(r"""<a\b[^>]*?href\s*=\s*["']([^"']+)["'][^>]*>(.*?)</a>""", re.DOTALL | re.IGNORECASE)
RST_LINK = re.compile(r"`([^`<]+?)\s*<([^>`]+)>`_")


@dataclass
class Found:
    url: str
    start: int | None = None
    end: int | None = None


def wrapper_pattern(wrappers=WRAPPERS):
    hosts = "|".join(re.escape(suffix) for suffix, _, _ in wrappers)
    return re.compile(rf"https?://(?:[\w-]+\.)*(?:{hosts})/[^\s\"'<>]+", re.IGNORECASE)


def key(url):
    return drive_id(url) or url.split("#")[0]


def find(text, shortlinks, wrappers=WRAPPERS):
    """
    Return the Drive URLs and short links in cleaned text, as Found, in order, each once.

    URLs inside redirect wrappers are unwrapped. A last pass over the percent-decoded text only fills gaps, since
    decoding can also break a URL that was fine (%20 becomes a space).
    """
    found, seen, wrapped = [], set(), []

    def add(url, span, raw=""):
        # An ellipsis marks a URL shortened for display.
        if raw.endswith("...") or url in seen:
            return
        seen.add(url)
        found.append(Found(url, *span) if span else Found(url))

    for m in wrapper_pattern(wrappers).finditer(text):
        url = strip(unwrap(html.unescape(m.group()), wrappers))
        if url != strip(html.unescape(m.group())):
            wrapped.append(m.span())
            if DRIVE.match(url):
                add(url, m.span())
            elif shortlinks.is_short(url):
                add(normalize_short(url), m.span())

    def inside(start):
        return any(s <= start < e for s, e in wrapped)

    for m in DRIVE.finditer(text):
        if not inside(m.start()):
            add(strip(m.group()), m.span(), m.group())
    for m in shortlinks.finditer(text):
        if not inside(m.start()):
            add(normalize_short(strip(m.group())), m.span(), m.group())

    keys = {key(f.url) for f in found if DRIVE.match(f.url)}
    decoded = clean(unquote(text))
    for m in DRIVE.finditer(decoded):
        url = strip(m.group())
        if key(url) not in keys and not m.group().endswith("..."):
            keys.add(key(url))
            add(url, None)
    for m in shortlinks.finditer(decoded):
        add(normalize_short(strip(m.group())), None, m.group())
    return found


def text_of(markup):
    """Return the text of HTML, or the alt text of its images if it has none."""
    text = re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", "", markup))).strip()
    if text:
        return text
    return " ".join(html.unescape(a) for a in re.findall(r"""\balt\s*=\s*["']([^"']+)["']""", markup)).strip()


def normalize_href(href, shortlinks, wrappers=WRAPPERS):
    """Return the URL that an href links to, as find() returns it."""
    url = strip(unwrap(html.unescape(unquote(href.strip())), wrappers))
    return normalize_short(url) if shortlinks.is_short(url) else url


def link_texts(text, shortlinks, patterns=(MD_LINK, HTML_LINK, RST_LINK), wrappers=WRAPPERS):
    """Return {URL: [text]} for the Markdown, HTML and reStructuredText links in the text."""
    texts = {}
    for pattern in patterns:
        for m in pattern.finditer(text):
            label, href = (m.group(2), m.group(1)) if pattern is HTML_LINK else (m.group(1), m.group(2))
            label = text_of(label) if pattern is HTML_LINK else label.strip()
            if label:
                texts.setdefault(normalize_href(href, shortlinks, wrappers), []).append(label)
    return texts
