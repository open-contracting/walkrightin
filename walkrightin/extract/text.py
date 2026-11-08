"""Find platforms' URLs and short links in text, tolerating markup, code and broken syntax."""

import html
import re
from dataclasses import dataclass
from urllib.parse import unquote

from walkrightin.urls import WRAPPERS, clean, normalize_short, strip, unwrap

MD_LINK = re.compile(r"\[([^\]]+)\]\(\s*<?([^\s)>]+)")
# An HTML link, or an OpenDocument link (text:a, with xlink:href).
HTML_LINK = re.compile(
    r"""<(?:text:)?a\b[^>]*?href\s*=\s*["']([^"']+)["'][^>]*>(.*?)</(?:text:)?a>""", re.DOTALL | re.IGNORECASE
)
RST_LINK = re.compile(r"`([^`<]+?)\s*<([^>`]+)>`_")


@dataclass
class Found:
    url: str
    start: int | None = None
    end: int | None = None


def wrapper_pattern(wrappers=WRAPPERS):
    hosts = "|".join(re.escape(suffix) for suffix, _, _ in wrappers)
    return re.compile(rf"https?://(?:[\w-]+\.)*(?:{hosts})/[^\s\"'<>]+", re.IGNORECASE)


def find(text, shortlinks, wrappers=WRAPPERS, platforms=None):
    """
    Return the platforms' URLs and short links in cleaned text, as Found, in order, each once.

    URLs inside redirect wrappers are unwrapped. A last pass over the percent-decoded text only fills gaps, since
    decoding can also break a URL that was fine (%20 becomes a space). ``platforms`` defaults to the short links'.
    """
    platforms = platforms or shortlinks.platforms
    found, seen, wrapped = [], set(), []

    def key(url):
        return platforms.key(url)[0] or url.split("#")[0]

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
            if platforms.match(url):
                add(url, m.span())
            elif shortlinks.is_short(url):
                add(normalize_short(url), m.span())

    def inside(start):
        return any(s <= start < e for s, e in wrapped)

    for m in platforms.finditer(text):
        if not inside(m.start()):
            add(strip(m.group()), m.span(), m.group())
    for m in shortlinks.finditer(text):
        if not inside(m.start()):
            add(normalize_short(strip(m.group())), m.span(), m.group())

    keys = {key(f.url) for f in found if platforms.match(f.url)}
    decoded = clean(unquote(text))
    for m in platforms.finditer(decoded):
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


def html_label(markup):
    """Return an anchor's text: "" if blank, or None if it has no text but shows something, like an image."""
    return text_of(markup) or (None if re.search(r"<(?:img|svg)\b", markup, re.IGNORECASE) else "")


def blank(labels):
    """Return whether a URL's anchors all have blank text, so that no reader sees the link."""
    return bool(labels) and all(label == "" for label in labels)


def joined_labels(labels):
    return " | ".join(dict.fromkeys(label for label in labels if label))


def link_texts(text, shortlinks, patterns=(MD_LINK, HTML_LINK, RST_LINK), wrappers=WRAPPERS):
    """Return {URL: [text]} for the Markdown, HTML and reStructuredText links in the text. See ``html_label``."""
    texts = {}
    for pattern in patterns:
        for m in pattern.finditer(text):
            label, href = (m.group(2), m.group(1)) if pattern is HTML_LINK else (m.group(1), m.group(2))
            label = html_label(label) if pattern is HTML_LINK else label.strip()
            texts.setdefault(normalize_href(href, shortlinks, wrappers), []).append(label)
    return texts
