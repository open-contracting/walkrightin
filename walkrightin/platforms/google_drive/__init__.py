"""Google Drive: files, folders, Docs, Sheets, Slides, Forms, Drawings, Colab notebooks and published documents."""

import csv
import dataclasses
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import ClassVar

from walkrightin.model import CLOSED_FORM, RATE_LIMITED, SIGNIN, Link
from walkrightin.platforms import Platform, Scan, join_key
from walkrightin.platforms.google_drive import readers
from walkrightin.platforms.google_drive.client import API_TYPES, DOWNLOADS, EXPORTS, SCOPES, Client
from walkrightin.platforms.google_drive.urls import (
    DOCUMENT,
    FOLDER,
    FORM,
    PATTERN,
    PUBLISHED_PREFIX,
    SPREADSHEET,
    bare_ids,
    check_url,
    continues,
    drive_id,
    file_url,
    is_drive_link,
    is_published,
    item_check_url,
    kind,
    location_of,
    published_id,
    resource_key,
    signin,
    throttled,
)
from walkrightin.sources.platform import PlatformSource

ANONYMOUS = "anonymous"


def norm(text):
    return re.sub(r"\s+", " ", text).strip().lower()


def read_map(path, key, value):
    if not path:
        return {}
    with Path(path).open(newline="") as f:
        return {r[key]: r[value] for r in csv.DictReader(f)}


@dataclass
class Options:
    #: A service account's key file, with domain-wide delegation. WALKRIGHTIN_GOOGLE_DRIVE_CREDENTIALS overrides it.
    credentials: str | None = None
    #: The users to act as, in order.
    subjects: list = field(default_factory=list)
    #: An owner in this domain is tried first.
    domain: str | None = None
    #: The scopes that the service account's domain-wide delegation authorizes. Read-only Drive is enough.
    scopes: list = field(default_factory=lambda: list(SCOPES))
    #: A CSV with ``driveid`` and ``user`` columns: the user to read each shared drive's permissions as, like an
    #: organizer, since a viewer sees none.
    drive_readers: str | None = None
    #: A CSV with ``published_id`` and ``file_id`` columns, to map published URLs (/d/e/...) to their files.
    aliases: str | None = None


class GoogleDrive(Platform):
    """
    Google Drive. Without ``credentials``, only public URLs are read.

    A published file's URL (/d/e/...) has no Drive ID: unless ``aliases`` maps it to its file, its ID is "e:" and its
    published ID, and it's checked and read through its published page.
    """

    name = "google_drive"
    pattern = PATTERN
    hosts = r"(?:docs|drive)\.google\."
    options = Options
    paths = ("drive_readers", "aliases")
    environ: ClassVar[dict] = {"credentials": "WALKRIGHTIN_GOOGLE_DRIVE_CREDENTIALS"}

    def __init__(self, options, context=None):
        super().__init__(options, context)
        self.client = Client(
            options.credentials,
            options.subjects,
            options.domain,
            options.scopes,
            user_agent=self.context.user_agent,
        )
        self.drive_readers = read_map(options.drive_readers, "driveid", "user")
        self.aliases = read_map(options.aliases, "published_id", "file_id")

    @property
    def credentialed(self):
        return self.client.credentialed

    @property
    def walk(self):
        return self.context.walk

    # Recognize

    def identifies(self, url):
        return is_drive_link(url)

    def key(self, url):
        """Return the Drive ID that a URL links to, or a published file's through the aliases or as "e:" and its ID."""
        if file_id := drive_id(url):
            return file_id, resource_key(url)
        if pid := published_id(url):
            return self.aliases.get(pid) or PUBLISHED_PREFIX + pid, resource_key(url)
        return "", resource_key(url)

    def url(self, item_id):
        return file_url(item_id)

    def bare_ids(self, text):
        return bare_ids(text)

    def continues(self, url):
        return continues(url)

    # Check

    def check_url(self, url):
        return check_url(url)

    def redirect_status(self, location):
        if signin(location):
            return SIGNIN
        if throttled(location):
            return RATE_LIMITED
        if "/closedform" in location:
            return CLOSED_FORM
        return None

    def item_check_url(self, item_id, meta):
        return item_check_url(item_id, meta["mime_type"])

    # Inspect

    def reader(self, drive, owner):
        """
        Return the user to read an item's permissions as: its shared drive's reader, or its owner if in the domain.

        Return None to read as whoever can see the item.
        """
        if drive:
            return self.drive_readers.get(drive)
        users = self.client.users_for(owner)
        return owner if owner and users and users[0] == owner else None

    def describe(self, item_id, resource_key, url):
        m = {"link_role": "?", "read_as": "", "mime_type": kind(url), "error": "", "resource_key": resource_key}
        # A published file has no Drive ID to read its metadata or permissions by. Its page is public or not.
        if is_published(item_id) or not self.credentialed:
            return m
        user, info = self.client.metadata(item_id, resource_key)
        if not info:
            m["error"] = "no access"
            return m
        owner = (info.get("owners") or [{}])[0].get("emailAddress", "")
        m.update(
            name=info.get("name", ""),
            mime_type=info.get("mimeType", ""),
            space=info.get("driveId", ""),
            owner=owner,
            modified=info.get("modifiedTime", ""),
            size=info.get("size", ""),
            read_as=user,
            version=info.get("md5Checksum") or info.get("headRevisionId") or info.get("modifiedTime", ""),
            resource_key=info.get("resourceKey") or resource_key,
            description=info.get("description", ""),
        )
        permission_reader = self.reader(m["space"], owner) or user
        m["link_role"], inherited, m["link_from"] = self.client.link(permission_reader, item_id)
        m["link_inherited"] = "1" if inherited else ""
        return m

    def after_check(self, meta, result):
        if not meta["mime_type"]:
            meta["mime_type"] = kind(result.get("location", ""))

    def owns(self, meta):
        """Return whether the organization owns the item: in a shared drive in drive_readers, or owned in domain."""
        if meta.get("space"):
            return meta["space"] in self.drive_readers
        domain = self.client.domain
        return bool(domain and meta.get("owner", "").lower().endswith("@" + domain))

    # Read

    def location(self, item_id, meta):
        return location_of(item_id, meta["mime_type"])

    def read(self, item_id, meta):
        mime = meta["mime_type"]
        user = meta["read_as"] or None
        key = meta.get("resource_key") or ""
        channels = self.walk.channels
        scans = []
        if is_published(item_id):
            if "export" in channels:
                found, error, note = self.page_links(item_id, location_of(item_id, mime), mime)
                scans.append(Scan("export", found, error, note, ANONYMOUS))
        elif mime in EXPORTS:
            if "export" in channels:
                with self.client.export(user, item_id, mime, key) as (path, error):
                    found, read_error = self.context.extractor.file(path) if path else ([], "")
                scans.append(Scan("export", found, error or read_error))
            if user and "api" in channels and mime in API_TYPES:
                scans.append(Scan("api", *self.api_links(user, item_id, mime)))
            if user and "comments" in channels:
                found, comments, error = self.comment_links(user, item_id, mime, key)
                scans.append(Scan("comments", found, error, comments=comments))
        elif mime == FORM:
            # A form's public page is what respondents see, if it accepts responses.
            if "export" in channels:
                found, error, note = self.page_links(item_id, item_check_url(item_id, mime), mime)
                scans.append(Scan("export", found, error, note, ANONYMOUS))
            if user and "api" in channels:
                scans.append(Scan("api", *self.form_links(user, item_id)))
        elif mime == FOLDER:
            if not user:
                if self.context.folder_limit != 0:
                    scans.append(Scan("listing", error="not walked: a folder can be listed only with credentials"))
            elif scan := self.listing(lambda limit: self.folder_links(user, item_id, limit)):
                scans.append(scan)
        elif mime and readers.is_google(mime):
            scans.append(Scan("none", note=f"not read: {mime}", read=False))
        else:
            found, error, note = self.download_links(user, item_id, meta, key)
            if found is None:
                scans.append(Scan("none", note=note, read=False))
            else:
                scans.append(Scan("download", found, error, note))
        return scans

    def local_key(self, url):
        return self.key(url)[0]

    def page_links(self, item_id, url, mime):
        """Return (Links, error, note) from a form's or a published file's public page."""
        final, html, error = self.client.page(url)
        if html is None:
            return [], error, ""
        if signin(final):
            return [], "sign-in", ""
        if "/closedform" in final:
            return [], "", "not read: the form doesn't accept responses"
        extractor = self.context.extractor
        if mime == FORM:
            texts = readers.form_page_texts(html)
            if texts is None:
                return [], "no form data in the page", ""
            found = extractor.text("\n".join(texts), "html")
        else:
            found = extractor.text(html, "html")
        # A page links to itself, and a form's page has each text with and without its HTML.
        own = {item_id, PUBLISHED_PREFIX + (published_id(final) or "")}
        best = {}
        for link in found:
            if self.local_key(link.url) in own:
                continue
            if link.url not in best or (link.text and not best[link.url].text):
                best[link.url] = link
        return list(best.values()), "", ""

    def form_links(self, user, item_id):
        """Return (Links, error) from the Forms API. A form that doesn't accept responses shows no one its links."""
        form, error = self.client.form(user, item_id)
        if form is None:
            return [], error
        visibility = "visible" if readers.accepting(form) else "hidden"
        found = []
        for container, text in readers.form_texts(form):
            found += self.context.extractor.text(text, part="body", container=container, visibility=visibility)
        return [link for link in found if self.local_key(link.url) != item_id], ""

    def api_links(self, user, item_id, mime):
        """Return (Links, error) from the Docs, Sheets or Slides API."""
        extractor = self.context.extractor
        if mime == DOCUMENT:
            doc, error = self.client.document(user, item_id)
            if doc is None:
                return [], error
            found = [
                Link("", url, part=part, container=title)
                for _, url, part, title in readers.doc_links(doc, detail=True)
            ]
        elif mime == SPREADSHEET:
            sheet, error = self.client.spreadsheet(user, item_id)
            if sheet is None:
                return [], error
            found = []
            for _, url, part, place, visibility, text in readers.sheet_links(sheet, detail=True):
                fields = {"part": part, "container": place, "visibility": visibility}
                if url:
                    found.append(Link("", url, **fields))
                else:
                    found += extractor.text(text, **fields)
        else:
            deck, error = self.client.presentation(user, item_id)
            if deck is None:
                return [], error
            found = [
                Link("", url, part=part, container=slide)
                for _, url, part, slide in readers.slide_links(deck, detail=True)
            ]
        return [link for link in found if extractor.is_link(link.url)], ""

    def comment_links(self, user, item_id, mime, key):
        """Return (Links in comments and replies, comments.csv rows, error)."""
        items, error = self.client.comments(user, item_id)
        found, rows = [], []
        text = None
        if mime == DOCUMENT and any(not c.get("resolved") and c.get("quotedFileContent") for c in items):
            with self.client.export(user, item_id, mime, key, text=True) as (path, _):
                if path:
                    text = norm(path.read_text(errors="ignore"))
        for c in items:
            part = "resolved_comment" if c.get("resolved") else "comment"
            quote = c.get("quotedFileContent", {}).get("value", "")
            for reply in [None, *c.get("replies", [])]:
                entry = reply or c
                if entry.get("deleted"):
                    continue
                body = entry.get("htmlContent") or entry.get("content") or ""
                container = c["id"] + (f"/{reply['id']}" if reply else "")
                found += self.context.extractor.text(body, "html", part=part, container=container)
                author = entry.get("author", {})
                rows.append(
                    {
                        "comment_id": c["id"],
                        "reply_id": reply["id"] if reply else "",
                        "author": author.get("displayName", ""),
                        "author_email": author.get("emailAddress", ""),
                        "me": "1" if author.get("me") else "",
                        "resolved": "1" if c.get("resolved") else "",
                        "created": entry.get("createdTime", ""),
                        "quote": "" if reply else quote,
                        "quote_present": ""
                        if reply or not quote or text is None
                        else ("1" if norm(quote)[:80] in text else "0"),
                    }
                )
        return found, rows, error

    def folder_links(self, user, item_id, limit):
        """Return (Links to the folder's items, up to ``limit`` + 1, error)."""
        children, error = self.client.children(user, item_id, limit)
        if error:
            return [], error
        found = []
        for child in children:
            url = location_of(child["id"], child.get("mimeType", ""))
            if child.get("resourceKey"):
                url += f"?resourcekey={child['resourceKey']}"
            found.append(Link("", url, child.get("name", ""), part="folder_child"))
        return found, ""

    def download_links(self, user, item_id, m, key):
        """Return (Links, error, note) for a non-Google file. If it isn't a type to read, Links is None."""
        mime, name = m["mime_type"], m.get("name", "")
        max_mb = self.walk.max_download_mb
        # A CSV can be uploaded with an Excel MIME type.
        csv_file = name.lower().endswith(".csv") or mime == "text/csv"
        if csv_file and not self.walk.download_csv:
            return None, "", "not read: a CSV file (see download_csv)"
        if user and not csv_file and mime not in DOWNLOADS:
            return None, "", f"not read: {mime or 'unknown type'}"
        if m.get("size") and int(m["size"]) > max_mb * 2**20:
            return [], "", f"skipped: larger than {max_mb} MB"
        cache_key = join_key(self.name, item_id)
        cached = self.context.downloads.get(cache_key)
        if cached and m.get("version") and cached.get("version") == m["version"]:
            return [Link(**link) for link in cached["links"]], cached.get("error", ""), "cached"
        ext = DOWNLOADS.get(mime, ".csv" if csv_file else "")
        with self.client.download(user, item_id, key, max_mb * 2**20, ext) as (path, error):
            if not path:
                note = "skipped: larger than" if error.startswith("larger than") else ""
                return [], error, note and f"{note} {max_mb} MB"
            if not user and not self.sniff(path):
                return None, "", "not read: not a PDF, Office or OpenDocument file, or notebook"
            found, read_error = self.context.extractor.file(path)
        if m.get("version"):
            self.context.downloads[cache_key] = {
                "version": m["version"],
                "links": [dataclasses.asdict(link) for link in found],
                "error": read_error,
            }
        return found, read_error, ""

    def sniff(self, path):
        """Return whether an anonymous download is a type to read: a PDF, Office or OpenDocument file, or notebook."""
        with path.open("rb") as f:
            head = f.read(8)
        if head.startswith((b"%PDF-", b"PK\x03\x04", b"\xd0\xcf\x11\xe0")) or path.suffix == ".ipynb":
            return True
        return bool(self.walk.download_csv and path.suffix == ".csv")


class GoogleDriveSource(PlatformSource):
    """
    Google Drive files, by ID, to read and walk from, whether or not they are public.

    Each file is a link at depth 0, located at "google_drive:ids". With ``follow`` (the default), the walk reads each
    file even if it doesn't open anonymously, like a private file to scan for links. It can read a private file only
    with credentials.
    """

    type = "google_drive"
    platform = "google_drive"
