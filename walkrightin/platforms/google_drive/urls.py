"""Find, identify and build Google Drive URLs."""

import re
from urllib.parse import parse_qs, unquote, urlsplit

from walkrightin.urls import END

# A Colab notebook's URL names its Drive file.
PATTERN = r"https?://(?:(?:drive|docs)\.google\.com/|colab\.research\.google\.com/drive/)"
DRIVE = re.compile(PATTERN + END)
DRIVE_ID = re.compile(r"(?:/d/|[?&]id=|/folders/|colab\.research\.google\.com/drive/)([A-Za-z0-9_-]{20,})")
PUBLISHED = re.compile(r"/d/e/([A-Za-z0-9_-]{10,})")
RESOURCE_KEY = re.compile(r"[?&]resourcekey=([\w-]+)")
# An identifier: a file's or folder's ID (a shared drive's is shorter), a published file's, or an old Sheet's key.
IDENTIFIED = re.compile(r"(/d/|/d/e/|id=|key=|/folders/|colab\.research\.google\.com/drive/)[-\w]{15,}")
# A file's ID outside a URL, as code opens a file by its ID: a current ID (33 or 44 characters) or an old one (28).
BARE = re.compile(r"(?<![-\w/=])(?:1[-\w]{32}|1[-\w]{43}|0[-\w]{27})(?![-\w])")
# A "Make a copy" URL asks every visitor to sign in, so its document is checked instead.
COPY = re.compile(r"/copy(?=$|[?#])")
GOOGLE_TYPES = {
    "document": "application/vnd.google-apps.document",
    "spreadsheets": "application/vnd.google-apps.spreadsheet",
    "presentation": "application/vnd.google-apps.presentation",
    "forms": "application/vnd.google-apps.form",
    "drawings": "application/vnd.google-apps.drawing",
}
DOCUMENT = GOOGLE_TYPES["document"]
SPREADSHEET = GOOGLE_TYPES["spreadsheets"]
PRESENTATION = GOOGLE_TYPES["presentation"]
FORM = GOOGLE_TYPES["forms"]
DRAWING = GOOGLE_TYPES["drawings"]
FOLDER = "application/vnd.google-apps.folder"
COLAB = "application/vnd.google.colaboratory"
EDIT_PATHS = {DOCUMENT: "document", SPREADSHEET: "spreadsheets", PRESENTATION: "presentation"}
# The URL of each type's published page, by its path in Docs URLs.
PUBLISHED_PAGES = {"document": "pub", "spreadsheets": "pubhtml", "presentation": "pub", "forms": "viewform"}
# A published file's ID: its published ID with a prefix, as it has no Drive ID.
PUBLISHED_PREFIX = "e:"


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
    """Return the URL of a published file's page, from its ID ("e:" and its published ID) and its type."""
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
    if is_published(file_id):
        return published_url(file_id, mime_type)
    if mime_type == FOLDER:
        return f"https://drive.google.com/drive/folders/{file_id}"
    if mime_type == FORM:
        # A form's permissions don't show who can respond; its own page does.
        return f"https://docs.google.com/forms/d/{file_id}/viewform"
    return file_url(file_id)


def location_of(item_id, mime_type):
    """Return the URL to name a Drive file by, as the location of the links in it."""
    if is_published(item_id):
        return published_url(item_id, mime_type)
    if mime_type in EDIT_PATHS:
        return f"https://docs.google.com/{EDIT_PATHS[mime_type]}/d/{item_id}/edit"
    if mime_type == FOLDER:
        return f"https://drive.google.com/drive/folders/{item_id}"
    return file_url(item_id)


def signin(url):
    return "accounts.google.com" in url or "/ServiceLogin" in url


def throttled(url):
    return "google.com/sorry" in url


def continues(url):
    """Return the URLs that a sign-in page's query continues to."""
    query = parse_qs(urlsplit(url).query)
    return [unquote(value) for value in query.get("continue", []) + query.get("followup", [])]
