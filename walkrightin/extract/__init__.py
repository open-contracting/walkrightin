"""Find links to platforms in text and files. Each format's URLs come from one raw pass; parsers add text and place."""

import json
import re
import zipfile
from pathlib import Path
from urllib.parse import unquote

from walkrightin.extract import markup, office, pdf
from walkrightin.extract.text import find, link_texts
from walkrightin.model import Link
from walkrightin.urls import WRAPPERS, clean

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
DOCUMENTS = (".pdf", ".doc", ".docx", ".ppt", ".pptx", ".xls", ".xlsx", ".odt", ".odp", ".ods")
# Notes on links found in a PDF's text where a URL-like fragment ends a line.
JOINED = "joined lines"
TRUNCATED = "possibly truncated"
CHECK = "check by hand"
OLE = b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"


def joined(value):
    """Return a notebook's text, which is a string or a list of lines."""
    return "".join(value) if isinstance(value, list) else str(value)


def kind_of(path):
    """Return the kind of text that a file's extension implies."""
    return KINDS.get(Path(path).suffix.lower(), "text")


class Extractor:
    """
    Find the platforms' URLs and short links in text and files, as Links without a location.

    Short links are returned as found. The walk resolves them, and keeps those that lead to a platform. The platforms
    are the short links'.
    """

    def __init__(self, shortlinks, *, bare_ids=False, wrappers=WRAPPERS):
        self.shortlinks = shortlinks
        self.bare_ids = bare_ids
        self.wrappers = wrappers

    @property
    def platforms(self):
        return self.shortlinks.platforms

    def is_link(self, url):
        return bool(self.platforms.match(url)) or self.shortlinks.is_short(url)

    def keep(self, url):
        return self.platforms.identifies(url) if self.platforms.match(url) else True

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
            linked = {self.platforms.key(f.url)[0] for f in found}
            links += [
                Link("", url, **{**fields, "part": "bare_id"})
                for key, url in sorted(self.platforms.bare_ids(content))
                if key not in linked
            ]
        return links

    def file(self, path, **fields):
        """Return (Links, error) for a PDF, an Office or OpenDocument file, or another file read as text."""
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
        if Path(path).suffix.lower() == ".ipynb":
            return self.notebook(data, **fields), ""
        return self.text(data.decode("utf-8", "replace"), kind_of(path), **fields), ""

    def pdf(self, path, data, **fields):
        body, error, split = pdf.read(path, data, self.platforms.hosts)
        links = {link.url: link for link in self.text(body, **fields)}
        for fragment, joined in split:
            rejoined = [link for link in self.text(joined, **fields) if self.leads_to_platform(link.url)]
            relevant = (
                re.fullmatch(r"https?:/*", fragment)
                or (self.platforms.hosts and self.platforms.hosts.search(fragment))
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

    def notebook(self, data, **fields):
        """Return a Link for each link in a Jupyter notebook's cells and their text outputs, with the cell number."""
        try:
            cells = json.loads(data).get("cells", [])
        except (ValueError, AttributeError):
            return self.text(data.decode("utf-8", "replace"), **fields)
        links = []
        for n, cell in enumerate(cells, 1):
            container = f"cell {n}"
            kind = "markdown" if cell.get("cell_type") == "markdown" else "text"
            links += self.text(joined(cell.get("source", "")), kind, **{"container": container, **fields})
            for output in cell.get("outputs", []):
                texts = {"text/plain": output["text"]} if "text" in output else output.get("data", {})
                for mime, value in texts.items():
                    if mime.startswith("text/"):
                        kind = {"text/html": "html", "text/markdown": "markdown"}.get(mime, "text")
                        links += self.text(joined(value), kind, **{"part": "output", "container": container, **fields})
        return links

    def leads_to_platform(self, url):
        return bool(self.platforms.match(url)) or bool(self.shortlinks.is_short(url) and self.shortlinks.resolve(url))

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
