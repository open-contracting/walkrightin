"""Find links to Drive in text and files. Every format's URLs come from one raw pass; parsers add text and place."""

import re
import zipfile
from pathlib import Path
from urllib.parse import unquote

from walkrightin.extract import markup, office, pdf
from walkrightin.extract.text import find, link_texts
from walkrightin.model import Link
from walkrightin.urls import DRIVE, WRAPPERS, bare_ids, clean, drive_id, file_url, is_drive_link

# Kinds of text, by file extension.
KINDS = {
    ".md": "markdown",
    ".markdown": "markdown",
    ".html": "html",
    ".htm": "html",
    ".xhtml": "html",
    ".rst": "rst",
}
# The documents to read as files rather than text.
DOCUMENTS = (".pdf", ".doc", ".docx", ".ppt", ".pptx", ".xls", ".xlsx")
# Notes on links found in a PDF's text where a URL-like fragment ends a line.
JOINED = "joined lines"
TRUNCATED = "possibly truncated"
CHECK = "check by hand"
GOOGLE = re.compile(r"(?:docs|drive)\.google\.")
OLE = b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"


def kind_of(path):
    """Return the kind of text that a file's extension implies."""
    return KINDS.get(Path(path).suffix.lower(), "text")


class Extractor:
    """
    Find the Drive URLs and short links in text and files, as Links without a location.

    Short links are returned as found. The walk resolves them, and keeps those that lead to Drive.
    """

    def __init__(self, shortlinks, *, bare_ids=False, wrappers=WRAPPERS):
        self.shortlinks = shortlinks
        self.bare_ids = bare_ids
        self.wrappers = wrappers

    def is_link(self, url):
        return bool(DRIVE.match(url)) or self.shortlinks.is_short(url)

    def keep(self, url):
        return is_drive_link(url) if DRIVE.match(url) else True

    def text(self, content, kind="text", **fields):
        """Return the Links in text of a kind: text, markdown, html or rst. ``fields`` are set on each Link."""
        content = clean(content)
        found = [f for f in find(content, self.shortlinks, self.wrappers) if self.keep(f.url)]
        if kind == "html":
            texts = markup.html_texts(content, self.shortlinks)
        elif kind == "markdown":
            texts = markup.markdown_texts(content, self.shortlinks)
        else:
            texts = link_texts(content, self.shortlinks, wrappers=self.wrappers)
        spans = markup.html_parts(content) if kind in ("html", "markdown") else []
        links = []
        for f in found:
            part, visibility = markup.place(f, spans) if spans else ("body", "visible")
            # An href is percent-decoded, so a URL found with "%3D" matches an href with "=".
            text = " | ".join(dict.fromkeys(texts.get(f.url, []) + texts.get(unquote(f.url), [])))
            links.append(Link("", f.url, text, **{"part": part, "visibility": visibility, **fields}))
        if self.bare_ids:
            linked = {drive_id(f.url) for f in found}
            links += [
                Link("", file_url(i), **{**fields, "part": "bare_id"})
                for i in sorted(bare_ids(content))
                if i not in linked
            ]
        return links

    def file(self, path, **fields):
        """Return (Links, error) for a PDF, an Office file, or another file read as text."""
        data = Path(path).read_bytes()
        if data.startswith(b"%PDF-"):
            return self.pdf(path, data, **fields)
        if zipfile.is_zipfile(path):
            links = self.text(
                office.contents(path), "html" if Path(path).suffix.lower() in {".html", ".htm"} else "text", **fields
            )
            return links + self.sheet(path, **fields), ""
        if data.startswith(OLE):
            return self.text(office.legacy(data), **fields), ""
        return self.text(data.decode("utf-8", "replace"), kind_of(path), **fields), ""

    def pdf(self, path, data, **fields):
        body, error, split = pdf.read(path, data)
        links = {link.url: link for link in self.text(body, **fields)}
        for fragment, joined in split:
            rejoined = [link for link in self.text(joined, **fields) if self.leads_to_drive(link.url)]
            relevant = (
                re.fullmatch(r"https?:/*", fragment)
                or GOOGLE.search(fragment)
                or any(True for _ in self.shortlinks.finditer(fragment))
            )
            if not rejoined and relevant:
                links.setdefault(joined, Link("", joined, note=f"{CHECK}: {fragment!r} ends a line", **fields))
            for link in rejoined:
                if link.url not in links:
                    links[link.url] = Link("", link.url, note=f"{JOINED}: {fragment!r} ends a line", **fields)
                # The URL as the text has it, if it ends at the line break, is probably cut short.
                for url, other in list(links.items()):
                    if url != link.url and link.url.startswith(url) and not other.note:
                        links[url] = Link("", url, other.text, note=f"{TRUNCATED} of {link.url}", **fields)
        return list(links.values()), error

    def leads_to_drive(self, url):
        return bool(DRIVE.match(url)) or bool(self.shortlinks.is_short(url) and self.shortlinks.resolve(url))

    def sheet(self, path, **fields):
        """Return a Link for each link in each spreadsheet tab's cells, with the tab and its visibility."""
        links = []
        try:
            cells = list(office.sheet_cells(path))
        except (KeyError, ValueError, zipfile.BadZipFile):
            return links
        for tab, visibility, ref, url, text in cells:
            urls = [url] if url and self.is_link(url) else [f.url for f in find(clean(text), self.shortlinks)]
            links.extend(
                Link(
                    "",
                    u,
                    text if url else "",
                    part="cell",
                    container=f"{tab}!{ref}",
                    visibility=visibility,
                    **fields,
                )
                for u in dict.fromkeys(urls)
                if self.keep(u)
            )
        return links
