"""
Read Drive files: anonymously, through public URLs, or with a service account's domain-wide delegation.

Also the readers of the Docs, Sheets and Slides APIs' links, which return each link with the API's own range, so that a
caller can write the links back.
"""

import re
import shutil
import tempfile
import threading
from contextlib import contextmanager
from pathlib import Path

import requests

from walkrightin.model import IDENTITY
from walkrightin.urls import FOLDER

DRIVE_API = "https://www.googleapis.com/drive/v3"
SCOPES = ["https://www.googleapis.com/auth/drive.readonly"]
DOCUMENT = "application/vnd.google-apps.document"
SPREADSHEET = "application/vnd.google-apps.spreadsheet"
PRESENTATION = "application/vnd.google-apps.presentation"
# The format to export each Google type to, as (extension, MIME type), and its public export URL.
EXPORTS = {
    DOCUMENT: ("html", "text/html", "https://docs.google.com/document/d/{id}/export?format=html"),
    SPREADSHEET: (
        "xlsx",
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        "https://docs.google.com/spreadsheets/d/{id}/export?format=xlsx",
    ),
    PRESENTATION: (
        "pptx",
        "application/vnd.openxmlformats-officedocument.presentationml.presentation",
        "https://docs.google.com/presentation/d/{id}/export/pptx",
    ),
}
TEXT_EXPORT = "https://docs.google.com/document/d/{id}/export?format=txt"
# The types to download. CSVs are left out: they hold data, whose URLs aren't meant as links.
DOWNLOADS = {
    "application/pdf": ".pdf",
    "application/msword": ".doc",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document": ".docx",
    "application/vnd.ms-powerpoint": ".ppt",
    "application/vnd.openxmlformats-officedocument.presentationml.presentation": ".pptx",
    "application/vnd.ms-excel": ".xls",
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet": ".xlsx",
}
METADATA_FIELDS = (
    "id,name,mimeType,driveId,owners(emailAddress),parents,modifiedTime,size,md5Checksum,headRevisionId,resourceKey"
)
COMMENT_FIELDS = (
    "nextPageToken,comments(id,author(displayName,emailAddress,me),resolved,deleted,createdTime,content,htmlContent,"
    "quotedFileContent,replies(id,author(displayName,emailAddress,me),deleted,createdTime,content,htmlContent))"
)
# The cell fields of a Sheet that hold links, with each tab's title and visibility.
SHEET_FIELDS = (
    "sheets(properties(sheetId,title,hidden),data(startRow,startColumn,rowData/values("
    "userEnteredValue,textFormatRuns,userEnteredFormat/textFormat/link,chipRuns,note)))"
)
CHUNK = 2**20


def headers(file_id, key):
    return {"X-Goog-Drive-Resource-Keys": f"{file_id}/{key}"} if key else {}


class Google:
    """
    Drive access. Without ``credentials``, only public URLs are read.

    ``credentials`` is a service account's key file, with domain-wide delegation for ``scopes``: by default, Drive's
    read-only scope, which the Docs, Sheets and Slides APIs also accept. ``subjects`` are the users to act as.
    """

    def __init__(self, credentials=None, subjects=(), domain=None, scopes=SCOPES, timeout=300, user_agent=IDENTITY):
        self.user_agent = user_agent
        self.key_file = Path(credentials).expanduser() if credentials else None
        self.scopes = list(scopes)
        self.subjects = list(subjects)
        self.domain = domain.lstrip("@").lower() if domain else None
        self.timeout = timeout
        self._local = threading.local()
        if self.key_file and not self.subjects:
            msg = "subjects are required with credentials"
            raise ValueError(msg)

    @property
    def credentialed(self):
        return self.key_file is not None

    def session(self, user=None):
        """Return a session acting as ``user``, or an anonymous session. Each thread has its own."""
        sessions = self._local.__dict__.setdefault("sessions", {})
        if user not in sessions:
            if user is None:
                sessions[user] = requests.Session()
                sessions[user].headers["User-Agent"] = self.user_agent
            else:
                from google.auth.transport.requests import AuthorizedSession  # noqa: PLC0415
                from google.oauth2 import service_account  # noqa: PLC0415

                credentials = service_account.Credentials.from_service_account_file(
                    self.key_file, scopes=self.scopes, subject=user
                )
                sessions[user] = AuthorizedSession(credentials)
        return sessions[user]

    def get(self, user, url, params=None, key_for=None, **kwargs):
        """Return (JSON or None, error text) for a GET request to an API."""
        try:
            response = self.session(user).get(
                url, params=params, headers=headers(*key_for) if key_for else {}, timeout=self.timeout, **kwargs
            )
        except requests.RequestException as e:
            return None, str(e)[:200]
        if not response.ok:
            return None, f"{response.status_code} {response.reason}"
        return response.json(), ""

    def pages(self, user, url, key, params):
        """Return (every item of a paginated list, error text)."""
        items, token = [], None
        while True:
            data, error = self.get(user, url, {**params, **({"pageToken": token} if token else {})})
            if data is None:
                return items, error
            items += data.get(key, [])
            if not (token := data.get("nextPageToken")):
                return items, ""

    def users_for(self, owner=""):
        """Return the users to try, in order: the owner if in the domain, then the subjects."""
        users = []
        if owner and self.domain and owner.lower().endswith("@" + self.domain):
            users.append(owner)
        return users + [s for s in self.subjects if s not in users]

    def metadata(self, file_id, key="", users=None):
        """Return (the first user who can see the item, its metadata) or (None, None)."""
        for user in users or self.subjects:
            f, _ = self.get(
                user,
                f"{DRIVE_API}/files/{file_id}",
                {"fields": METADATA_FIELDS, "supportsAllDrives": "true"},
                key_for=(file_id, key),
            )
            if f:
                return user, f
        return None, None

    def link_role(self, user, file_id):
        """Return the item's anyone-with-link role, "" if it has none, or "?" if its permissions can't be read."""
        permissions, error = self.pages(
            user,
            f"{DRIVE_API}/files/{file_id}/permissions",
            "permissions",
            {"fields": "nextPageToken,permissions(id,type,role)", "supportsAllDrives": "true", "pageSize": 100},
        )
        # A viewer who isn't the owner sees no permissions.
        if error or not permissions:
            return "?"
        return next((p["role"] for p in permissions if p.get("type") == "anyone"), "")

    @contextmanager
    def export(self, user, file_id, mime_type, key="", *, text=False):
        """Export a Doc, Sheet or deck to a temporary file, deleted after. Yield (path or None, error)."""
        ext, export_type, public = EXPORTS[mime_type]
        if text:
            ext, export_type, public = "txt", "text/plain", TEXT_EXPORT
        if user:
            # Export links have no size limit, unlike files.export.
            f, error = self.get(
                user,
                f"{DRIVE_API}/files/{file_id}",
                {"fields": "exportLinks", "supportsAllDrives": "true"},
                key_for=(file_id, key),
            )
            url = (f or {}).get("exportLinks", {}).get(export_type)
            if not url:
                yield None, error or f"no {ext} export"
                return
        else:
            url = public.format(id=file_id) + (f"&resourcekey={key}" if key and "?" in public else "")
        with self._fetch(user, url, ext, file_id, key=key) as result:
            yield result

    @contextmanager
    def download(self, user, file_id, key="", max_bytes=None, ext=""):
        """Download a file to a temporary file, deleted after. Yield (path or None, error)."""
        if user:
            url = f"{DRIVE_API}/files/{file_id}?alt=media&supportsAllDrives=true"
        else:
            url = f"https://drive.usercontent.google.com/download?id={file_id}&export=download&confirm=t"
            if key:
                url += f"&resourcekey={key}"
        with self._fetch(user, url, ext, file_id, key=key, max_bytes=max_bytes) as result:
            yield result

    @contextmanager
    def _fetch(self, user, url, ext, file_id, key="", max_bytes=None):
        tmp = tempfile.mkdtemp(prefix="walkrightin-")
        try:
            try:
                response = self.session(user).get(
                    url, headers=headers(file_id, key) if user else {}, timeout=self.timeout, stream=True
                )
            except requests.RequestException as e:
                yield None, str(e)[:200]
                return
            with response:
                if not response.ok:
                    yield None, f"{response.status_code} {response.reason}{'' if key else ' (no resource key)'}"
                    return
                if not user and "text/html" in response.headers.get("Content-Type", "") and ext != "html":
                    yield None, "sign-in or warning page"
                    return
                if not ext:
                    ext = disposition_ext(response.headers.get("Content-Disposition", ""))
                path = Path(tmp) / (file_id + (f".{ext.lstrip('.')}" if ext else ""))
                if error := save(response, path, max_bytes):
                    yield None, error
                    return
            yield path, ""
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    def children(self, user, folder_id, limit):
        """Return (the folder's items, not trashed, up to ``limit`` + 1, error text)."""
        items, token = [], None
        while True:
            params = {
                "q": f"'{folder_id}' in parents and trashed = false",
                "fields": "nextPageToken,files(id,name,mimeType,resourceKey)",
                "pageSize": min(1000, limit + 1),
                "supportsAllDrives": "true",
                "includeItemsFromAllDrives": "true",
                "corpora": "allDrives",
            }
            if token:
                params["pageToken"] = token
            data, error = self.get(user, f"{DRIVE_API}/files", params)
            if data is None:
                return items, error
            items += data.get("files", [])
            if len(items) > limit or not (token := data.get("nextPageToken")):
                return items[: limit + 1], ""

    def comments(self, user, file_id):
        """Return (the file's comments, with replies, not deleted, error text)."""
        items, error = self.pages(
            user, f"{DRIVE_API}/files/{file_id}/comments", "comments", {"fields": COMMENT_FIELDS, "pageSize": 100}
        )
        return [c for c in items if not c.get("deleted")], error

    def document(self, user, doc_id, suggestions="SUGGESTIONS_INLINE"):
        return self.get(
            user,
            f"https://docs.googleapis.com/v1/documents/{doc_id}",
            {"includeTabsContent": "true", "suggestionsViewMode": suggestions},
        )

    def spreadsheet(self, user, sheet_id):
        return self.get(
            user,
            f"https://sheets.googleapis.com/v4/spreadsheets/{sheet_id}",
            {"includeGridData": "true", "fields": SHEET_FIELDS},
        )

    def presentation(self, user, presentation_id):
        return self.get(user, f"https://slides.googleapis.com/v1/presentations/{presentation_id}")


def save(response, path, max_bytes):
    """Write a streamed response to the path. Return the error text, or the empty string."""
    size = 0
    try:
        with path.open("wb") as out:
            for chunk in response.iter_content(CHUNK):
                size += len(chunk)
                if max_bytes is not None and size > max_bytes:
                    return f"larger than {max_bytes // 2**20} MB"
                out.write(chunk)
    except requests.RequestException as e:
        return str(e)[:200]
    return ""


def disposition_ext(value):
    m = re.search(r"""filename\*?=(?:UTF-8'')?"?([^";]+)"?""", value)
    return Path(m.group(1)).suffix.lstrip(".").lower() if m else ""


def is_google(mime_type):
    return mime_type.startswith("application/vnd.google-apps.")


def is_folder(mime_type):
    return mime_type == FOLDER


def doc_links(doc, *, detail=False):
    """
    Return the Doc's links as (range, URL): its text runs', and its smart chips' with a range of None.

    With ``detail``, return (range, URL, part, tab title): part is body, header, footer, footnote, chip or suggestion.
    A suggested link is one in a suggested insertion, or in a suggested change to a run's style.
    """
    found, plain = [], []

    def walk(content, where, part, title):
        for element in content or []:
            if "table" in element:
                for row in element["table"].get("tableRows", []):
                    for cell in row.get("tableCells", []):
                        walk(cell.get("content"), where, part, title)
            for e in element.get("paragraph", {}).get("elements", []):
                run = e.get("textRun", {})
                span = {**where, "startIndex": e.get("startIndex", 0), "endIndex": e.get("endIndex")}
                if url := run.get("textStyle", {}).get("link", {}).get("url"):
                    found.append((span, url, "suggestion" if run.get("suggestedInsertionIds") else part, title))
                    plain.append((span, url))
                elif uri := e.get("richLink", {}).get("richLinkProperties", {}).get("uri"):
                    found.append((None, uri, "chip", title))
                    plain.append((None, uri))
                found.extend(
                    (span, url, "suggestion", title)
                    for change in run.get("suggestedTextStyleChanges", {}).values()
                    if (url := change.get("textStyle", {}).get("link", {}).get("url"))
                )

    def tabs(items):
        for tab in items:
            properties = tab.get("tabProperties", {})
            where = {"tabId": properties.get("tabId")}
            title = properties.get("title", "")
            body = tab.get("documentTab", {})
            walk(body.get("body", {}).get("content"), where, "body", title)
            for kind, part in (("headers", "header"), ("footers", "footer"), ("footnotes", "footnote")):
                for segment_id, segment in body.get(kind, {}).items():
                    walk(segment.get("content"), {**where, "segmentId": segment_id}, part, title)
            tabs(tab.get("childTabs", []))

    tabs(doc.get("tabs", []))
    return found if detail else plain


def column(index):
    """Return a column's letters, from its 0-based index."""
    letters = ""
    index += 1
    while index:
        index, rest = divmod(index - 1, 26)
        letters = chr(65 + rest) + letters
    return letters


def sheet_links(sheet, *, detail=False):
    """
    Return the Sheet's links as (cell, URL): cells', runs' and HYPERLINK formulas', and smart chips' with None.

    With ``detail``, return (cell, URL or None, part, "Tab!A1", visibility, text): part is cell, chip or note. A note,
    and a cell's string value, are returned with a URL of None and their text, for the caller to find URLs in.
    """
    from walkrightin.urls import DRIVE  # noqa: PLC0415

    found = []
    for tab in sheet.get("sheets", []):
        properties = tab.get("properties", {})
        sheet_id = properties.get("sheetId", 0)
        title = properties.get("title", "")
        visibility = "hidden" if properties.get("hidden") else "visible"
        for data in tab.get("data", []):
            for r, row in enumerate(data.get("rowData", []), data.get("startRow", 0)):
                for c, value in enumerate(row.get("values", []), data.get("startColumn", 0)):
                    place = f"{title}!{column(c)}{r + 1}"
                    runs = value.get("textFormatRuns", [])
                    urls = [value.get("userEnteredFormat", {}).get("textFormat", {}).get("link", {}).get("uri")]
                    urls += [run.get("format", {}).get("link", {}).get("uri") for run in runs]
                    urls += DRIVE.findall(value.get("userEnteredValue", {}).get("formulaValue", ""))
                    cell = {"sheetId": sheet_id, "row": r, "column": c, "value": value}
                    found += [
                        ({**cell, "url": url}, url, "cell", place, visibility, "")
                        for url in dict.fromkeys(urls)
                        if url
                    ]
                    found += [
                        (None, uri, "chip", place, visibility, "")
                        for run in value.get("chipRuns", [])
                        if (uri := run.get("chip", {}).get("richLinkProperties", {}).get("uri"))
                    ]
                    if note := value.get("note"):
                        found.append((None, None, "note", place, visibility, note))
                    if text := value.get("userEnteredValue", {}).get("stringValue"):
                        found.append((None, None, "cell", place, visibility, text))
    if detail:
        return found
    return [(where, url) for where, url, part, *_ in found if url and part != "note"]


def slide_links(deck, *, detail=False):
    """
    Return the deck's links as (place, URL): text runs', and shapes', lines' and images', on slides and notes.

    With ``detail``, return (place, URL, part, slide ID): part is text, shape, line, image or speaker_notes.
    """
    found = []

    def text(body, where, part, slide):
        for e in body.get("textElements", []):
            if url := e.get("textRun", {}).get("style", {}).get("link", {}).get("url"):
                span = {"type": "FIXED_RANGE", "startIndex": e.get("startIndex", 0), "endIndex": e["endIndex"]}
                found.append(({**where, "kind": "text", "textRange": span}, url, part or "text", slide))

    def walk(elements, part, slide):
        for element in elements or []:
            where = {"objectId": element["objectId"]}
            found.extend(
                ({**where, "kind": kind}, url, part or kind, slide)
                for kind in ("shape", "line", "image")
                if (url := element.get(kind, {}).get(f"{kind}Properties", {}).get("link", {}).get("url"))
            )
            text(element.get("shape", {}).get("text", {}), where, part, slide)
            for row in element.get("table", {}).get("tableRows", []):
                for cell in row.get("tableCells", []):
                    text(cell.get("text", {}), {**where, "cellLocation": cell["location"]}, part, slide)
            walk(element.get("elementGroup", {}).get("children"), part, slide)

    for page in deck.get("slides", []):
        slide = page.get("objectId", "")
        walk(page.get("pageElements"), None, slide)
        walk(page.get("slideProperties", {}).get("notesPage", {}).get("pageElements"), "speaker_notes", slide)
    if detail:
        return found
    return [(where, url) for where, url, *_ in found]
