"""Attach link text to URLs in HTML and Markdown, and tag where in the markup each URL is."""

import re

from markdown_it import MarkdownIt

from walkrightin.extract.text import HTML_LINK, MD_LINK, normalize_href, text_of

COMMENT = re.compile(r"<!--.*?-->", re.DOTALL)
SCRIPT = re.compile(r"<(script|style)\b[^>]*>.*?</\1\s*>", re.DOTALL | re.IGNORECASE)
TAG = re.compile(r"""<([a-zA-Z][\w:-]*)\b((?:[^>"']|"[^"]*"|'[^']*')*)>""")
ATTRIBUTE = re.compile(r"""([\w:-]+)\s*=\s*("[^"]*"|'[^']*'|[^\s>]+)""")
# Attributes whose URL a visitor follows or sees rendered, like an embedded Doc.
VISIBLE_ATTRIBUTES = {"href", "src", "action", "data", "poster"}


def html_parts(text):
    """Return [(start, end, part, visibility)] for comments, scripts and styles, and tags' attribute values."""
    spans = [(m.start(), m.end(), "html_comment", "source_only") for m in COMMENT.finditer(text)]
    spans += [(m.start(), m.end(), m.group(1).lower(), "source_only") for m in SCRIPT.finditer(text)]
    for m in TAG.finditer(text):
        name = m.group(1).lower()
        offset = m.start(2)
        for a in ATTRIBUTE.finditer(m.group(2)):
            attribute = a.group(1).lower()
            part = "body" if name == "a" and attribute == "href" else f"attribute:{attribute}"
            visibility = "visible" if attribute in VISIBLE_ATTRIBUTES else "source_only"
            spans.append((offset + a.start(2), offset + a.end(2), part, visibility))
    return spans


def place(found, spans):
    """Return (part, visibility) for a Found URL, given html_parts()."""
    if found.start is None:
        return "body", "visible"
    # Comments and scripts contain tags, so they are checked first.
    for start, end, part, visibility in spans:
        if start <= found.start < end and visibility == "source_only" and part in ("html_comment", "script", "style"):
            return part, visibility
    for start, end, part, visibility in spans:
        if start <= found.start < end:
            return part, visibility
    return "body", "visible"


def html_texts(text, shortlinks):
    """Return {URL: [text]} for the HTML anchors, using an image's alt text for an image wrapped in a link."""
    texts = {}
    for m in HTML_LINK.finditer(text):
        if label := text_of(m.group(2)):
            texts.setdefault(normalize_href(m.group(1), shortlinks), []).append(label)
    return texts


MARKDOWN = MarkdownIt("commonmark")


def markdown_texts(text, shortlinks):
    """Return {URL: [text]} for the Markdown links, parsed, with a regex for links the parser rejects."""
    texts = {}
    try:
        tokens = MARKDOWN.parse(text)
    except Exception:  # noqa: BLE001 # broken Markdown falls back to the regex
        tokens = []
    for token in tokens:
        if token.type != "inline" or not token.children:
            continue
        href, label = None, []
        for child in token.children:
            if child.type == "link_open":
                href, label = child.attrGet("href"), []
            elif child.type == "link_close" and href:
                if joined := "".join(label).strip():
                    texts.setdefault(normalize_href(str(href), shortlinks), []).append(joined)
                href = None
            elif href is not None and child.type in ("text", "code_inline"):
                label.append(child.content)
            elif href is not None and child.type == "image":
                label.append(child.content or child.attrGet("alt") or "")
    for m in MD_LINK.finditer(text):
        url = normalize_href(m.group(2), shortlinks)
        if url not in texts:
            texts[url] = [m.group(1).strip()]
    return texts
