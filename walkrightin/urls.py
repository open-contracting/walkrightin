"""Find, clean and identify Google Drive URLs."""

import html
import re
from urllib.parse import parse_qs, urlsplit

# The characters that end a URL in running text, markup or code.
END = r"""[^\s"'<>\\(){}|\[\]]+"""
# A Colab notebook's URL names its Drive file.
DRIVE = re.compile(r"https?://(?:(?:drive|docs)\.google\.com/|colab\.research\.google\.com/drive/)" + END)
DRIVE_ID = re.compile(r"(?:/d/|[?&]id=|/folders/|colab\.research\.google\.com/drive/)([A-Za-z0-9_-]{20,})")
PUBLISHED = re.compile(r"/d/e/([A-Za-z0-9_-]{10,})")
RESOURCE_KEY = re.compile(r"[?&]resourcekey=([\w-]+)")
# An identifier: a file's or folder's ID (a shared drive's is shorter), a published file's, or an old Sheet's key.
IDENTIFIED = re.compile(r"(/d/|/d/e/|id=|key=|/folders/|colab\.research\.google\.com/drive/)[-\w]{15,}")
# A file's ID outside a URL, as code opens a file by its ID: a current ID (33 or 44 characters) or an old one (28).
BARE = re.compile(r"(?<![-\w/=])(?:1[-\w]{32}|1[-\w]{43}|0[-\w]{27})(?![-\w])")
# A "Make a copy" URL asks every visitor to sign in, so its document is checked instead.
COPY = re.compile(r"/copy(?=$|[?#])")
# Characters that end a URL, as entities in markup.
ENCODED_END = re.compile(r"&(?:quot|apos|lt|gt|#34|#39|#x22|#x27);", re.IGNORECASE)
UNICODE_ESCAPE = re.compile(r"\\+u([0-9a-fA-F]{4})")
# Redirect wrappers, as (host suffix, path, query parameters holding the destination).
WRAPPERS = [
    ("google.com", "/url", ("q", "url")),
    ("safelinks.protection.outlook.com", "/", ("url",)),
    ("l.facebook.com", "/l.php", ("u",)),
]
GOOGLE_TYPES = {
    "document": "application/vnd.google-apps.document",
    "spreadsheets": "application/vnd.google-apps.spreadsheet",
    "presentation": "application/vnd.google-apps.presentation",
    "forms": "application/vnd.google-apps.form",
    "drawings": "application/vnd.google-apps.drawing",
}
FOLDER = "application/vnd.google-apps.folder"
FORM = GOOGLE_TYPES["forms"]
DRAWING = GOOGLE_TYPES["drawings"]
COLAB = "application/vnd.google.colaboratory"
# The URL of each type's published page, by its path in Docs URLs.
PUBLISHED_PAGES = {"document": "pub", "spreadsheets": "pubhtml", "presentation": "pub", "forms": "viewform"}
# A published file's item ID: its published ID with a prefix, as it has no Drive ID.
PUBLISHED_PREFIX = "e:"


def clean(text):
    r"""Decode the JSON escapes in markup and code (\u002D, \/), and drop line-break hints."""
    text = UNICODE_ESCAPE.sub(lambda m: chr(int(m.group(1), 16)), text)
    text = re.sub(r"\\+/", "/", text)
    # Link text like "https://bit.ly/ocds-api-<wbr />discussion" would otherwise end at the tag.
    return re.sub(r"<wbr\s*/?>|&shy;|\u00ad", "", text)


def strip(url):
    """Cut the URL at an encoded quote or bracket; decode entities; drop zero-width characters and end punctuation."""
    url = ENCODED_END.split(url, maxsplit=1)[0]
    return re.sub("[\u200b-\u200d\ufeff]", "", html.unescape(url)).rstrip(".,;:!")


def unwrap(url, wrappers=WRAPPERS):
    """Return the destination of a redirect wrapper, like google.com/url?q=..., or the URL."""
    try:
        parts = urlsplit(url)
    except ValueError:  # an href like "[r]"
        return url
    host = parts.netloc.lower().split(":")[0]
    for suffix, path, params in wrappers:
        if (host == suffix or host.endswith("." + suffix)) and parts.path == path:
            query = parse_qs(parts.query)
            for param in params:
                if query.get(param):
                    return query[param][0]
    return url


def is_wrapper(url, wrappers=WRAPPERS):
    return unwrap(url, wrappers) != url


def drive_id(url):
    """Return the Drive ID in a URL, or None."""
    m = DRIVE_ID.search(url or "")
    return m.group(1) if m else None


def published_id(url):
    """Return the ID in a published file's URL (/d/e/...), or None."""
    m = PUBLISHED.search(url or "")
    return m.group(1) if m else None


def is_published(item_id):
    return item_id.startswith(PUBLISHED_PREFIX)


def published_url(item_id, mime_type):
    """Return the URL of a published file's page, from its item ID ("e:" and its published ID) and its type."""
    path = next((k for k, v in GOOGLE_TYPES.items() if v == mime_type), "document")
    page = PUBLISHED_PAGES.get(path, "pub")
    return f"https://docs.google.com/{path}/d/e/{item_id.removeprefix(PUBLISHED_PREFIX)}/{page}"


def resource_key(url):
    """Return the resource key in the URL, or the empty string."""
    m = RESOURCE_KEY.search(url or "")
    return m.group(1) if m else ""


def is_drive_link(url):
    """Return whether a Drive URL identifies an item, unlike a template ("/document/d/$") or a page."""
    return bool(IDENTIFIED.search(url))


def bare_ids(text):
    """Return the strings in the text that look like a file's ID, mixing lowercase, uppercase and digits."""
    return {i for i in BARE.findall(text) if re.search(r"[a-z]", i) and re.search(r"[A-Z]", i) and re.search(r"\d", i)}


def file_url(file_id):
    return f"https://drive.google.com/file/d/{file_id}/view"


def kind(url):
    """Return the Google MIME type that a Docs URL implies, FOLDER for a folder URL, or ""."""
    if "/drive/folders/" in url or "/folderview" in url:
        return FOLDER
    if url.startswith(("https://colab.research.google.com/", "http://colab.research.google.com/")):
        return COLAB
    m = re.match(r"https?://docs\.google\.com/(\w+)/", url)
    return GOOGLE_TYPES.get(m.group(1), "") if m else ""


def check_url(url):
    """Return the URL to request to check the URL anonymously."""
    # Colab's page opens for anyone, whoever can open the notebook.
    if kind(url) == COLAB and (file_id := drive_id(url)):
        return file_url(file_id)
    return COPY.sub("/edit", url)


def item_check_url(file_id, mime_type):
    """Return the URL to request to check an item anonymously, by its type."""
    if mime_type == FOLDER:
        return f"https://drive.google.com/drive/folders/{file_id}"
    if mime_type == FORM:
        # A form's permissions don't show who can respond; its own page does.
        return f"https://docs.google.com/forms/d/{file_id}/viewform"
    return file_url(file_id)


def normalize_short(url):
    return re.sub(r"^(?:https?://)?(?:www\.)?", "https://", url).rstrip("/")
