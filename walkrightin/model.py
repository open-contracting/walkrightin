"""Records passed between sources, extractors, the walk and the outputs."""

from dataclasses import dataclass
from importlib.metadata import version

# The User-Agent of every request.
IDENTITY = f"walkrightin/{version('walkrightin')} (+https://github.com/open-contracting/walkrightin)"

# How an anonymous visitor sees a URL.
PUBLIC = "public"
REDIRECT = "redirect"  # redirects to a page that opens
SIGNIN = "signin"
NOT_FOUND = "not_found"
GONE = "gone"  # 410: deleted
CLOSED_FORM = "closed_form"
RATE_LIMITED = "rate_limited"
ERROR = "error"
STATUSES = (PUBLIC, REDIRECT, SIGNIN, NOT_FOUND, GONE, CLOSED_FORM, RATE_LIMITED, ERROR)
# Statuses for which a file exists, public or not.
FOUND = {PUBLIC, REDIRECT, SIGNIN}


def opens(status):
    """Return whether an anonymous visitor can open the URL."""
    return status in (PUBLIC, REDIRECT)


@dataclass(frozen=True, slots=True)
class Link:
    """A link found in a source, or in an item reached by the walk."""

    #: Where the link was found, as the source names it: a URL when the source knows one cheaply.
    location: str
    #: The URL as found. It may be a short link, or wrapped in a redirect like google.com/url.
    url: str
    #: The link's text, or "" for a bare URL.
    text: str = ""
    #: Where in the location: body, header, footer, footnote, chip, suggestion, cell, note, comment, speaker_notes,
    #: script, html_comment, attribute:NAME, folder_child, bare_id, ...
    part: str = "body"
    #: Where inside the location: a tab, a sheet and cell, a slide, a comment.
    container: str = ""
    #: visible, hidden, very_hidden (a hidden spreadsheet tab), source_only (only in an HTML page's source), or blank
    #: (on text that is empty or only whitespace, everywhere the item links the URL).
    visibility: str = "visible"
    #: How an item was read: export, download, api, comments, listing or metadata. Empty for sources.
    channel: str = ""
    #: An extractor's note, like a URL joined across lines in a PDF.
    note: str = ""
