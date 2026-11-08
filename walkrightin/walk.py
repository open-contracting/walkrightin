"""Run the sources, resolve and check their links, and walk the Drive files they reach."""

import csv
import dataclasses
import logging
import re
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path

import requests

from walkrightin import google as g
from walkrightin.check import Checker, now, signin
from walkrightin.extract import Extractor
from walkrightin.model import FOUND, IDENTITY, Link, opens
from walkrightin.output import (
    COMMENT_FIELDS,
    ITEM_FIELDS,
    LINK_FIELDS,
    SCAN_FIELDS,
    read_csv,
    read_json,
    write_csv,
    write_json,
)
from walkrightin.shortlinks import ShortLinks
from walkrightin.sources import Context, create
from walkrightin.urls import (
    DRIVE,
    FOLDER,
    FORM,
    PUBLISHED_PREFIX,
    WRAPPERS,
    drive_id,
    file_url,
    is_published,
    item_check_url,
    kind,
    published_id,
    published_url,
    resource_key,
)

logger = logging.getLogger(__name__)

WALK = "walk"
ANONYMOUS = "anonymous"
EDIT_PATHS = {g.DOCUMENT: "document", g.SPREADSHEET: "spreadsheets", g.PRESENTATION: "presentation"}


def location_of(item_id, mime_type):
    """Return the URL to name a Drive file by, as the location of the links in it."""
    if is_published(item_id):
        return published_url(item_id, mime_type)
    if mime_type in EDIT_PATHS:
        return f"https://docs.google.com/{EDIT_PATHS[mime_type]}/d/{item_id}/edit"
    if mime_type == FOLDER:
        return f"https://drive.google.com/drive/folders/{item_id}"
    return file_url(item_id)


def norm(text):
    return re.sub(r"\s+", " ", text).strip().lower()


@dataclass
class Result:
    links: int = 0
    items: int = 0
    rate_limited: int = 0
    skipped_for_size: dict = field(default_factory=dict)
    skipped_for_outside_hops: int = 0


class Run:
    """
    One run into ``out``, a directory.

    With ``only`` (source names) and an existing run in ``out``, only those sources run again; the others' links are
    kept. With ``resume``, an interrupted walk continues from its last complete level. Without ``walk``, only the
    sources run, and their links are checked.
    """

    def __init__(self, config, out, *, only=None, resume=False, walk=True):
        self.config = config
        self.out = out
        self.only = set(only or ())
        self.resume = resume
        self.walk_enabled = walk
        self.out = Path(out)
        self.out.mkdir(parents=True, exist_ok=True)
        cache = Path(config.cache_dir)
        cache.mkdir(parents=True, exist_ok=True)
        self.session = requests.Session()
        self.session.headers["User-Agent"] = IDENTITY
        self.shortlinks = ShortLinks(
            config.short_links.domains,
            config.short_links.extra_domains,
            config.short_links.exclude_domains,
            session=self.session,
        )
        self.shortlinks_cache = cache / "shortlinks.json"
        self.shortlinks.load(self.shortlinks_cache)
        self.wrappers = [*WRAPPERS, *((w[0], w[1], tuple(w[2])) for w in config.wrappers)]
        self.extractor = Extractor(self.shortlinks, wrappers=self.wrappers)
        self.checker = Checker(
            cache / "checks.json",
            ttl_days=config.check.ttl_days,
            concurrency=config.check.concurrency,
            delay=config.check.delay,
            session=self.session,
            user_agent=config.check.user_agent,
        )
        gc = config.google
        self.google = g.Google(gc.credentials, gc.subjects, gc.domain, gc.scopes, user_agent=config.check.user_agent)
        self.drive_readers = self.read_drive_readers(gc.drive_readers)
        self.max_download = config.walk.max_download_mb * 2**20
        self.folder_limit = config.folder_limit
        self.channels = set(config.walk.channels)
        self.downloads_cache_path = cache / "downloads.json"
        self.downloads_cache = read_json(self.downloads_cache_path, {})
        self.aliases = self.read_aliases(config.walk.aliases)
        self.also_follow = self.read_ids(config.walk.also_follow)
        self.result = Result()
        self.path = lambda name: self.out / name

    # Inputs

    @staticmethod
    def read_ids(path):
        if not path:
            return set()
        lines = Path(path).read_text().splitlines()
        return {line.strip() for line in lines if line.strip() and not line.startswith("#")}

    @staticmethod
    def read_aliases(path):
        if not path:
            return {}
        with Path(path).open(newline="") as f:
            return {r["published_id"]: r["file_id"] for r in csv.DictReader(f)}

    @staticmethod
    def read_drive_readers(path):
        if not path:
            return {}
        with Path(path).open(newline="") as f:
            return {r["driveid"]: r["user"] for r in csv.DictReader(f)}

    def reader(self, drive, owner):
        """
        Return the user to read an item's permissions as: its shared drive's reader, or its owner if in the domain.

        Return None to read as whoever can see the item.
        """
        if drive:
            return self.drive_readers.get(drive)
        users = self.google.users_for(owner)
        return owner if owner and users and users[0] == owner else None

    # Links

    def target(self, url):
        """Return (Drive URL or None, short URL or "") for a link's URL."""
        if DRIVE.match(url):
            return url, ""
        if self.shortlinks.is_short(url):
            return self.shortlinks.resolve(url), url
        return None, ""

    def item_id(self, url):
        """Return the Drive ID that a URL links to, or a published file's through the aliases or as "e:" and its ID."""
        if file_id := drive_id(url):
            return file_id
        if pid := published_id(url):
            return self.aliases.get(pid) or PUBLISHED_PREFIX + pid
        return ""

    def resolve(self, links):
        """Resolve short links in parallel, so that target() finds them cached."""
        shorts = sorted(
            {link.url for _, link, _ in links if not DRIVE.match(link.url) and self.shortlinks.is_short(link.url)}
        )
        with ThreadPoolExecutor(8) as pool:
            list(pool.map(self.shortlinks.follow, shorts))
        self.shortlinks.save(self.shortlinks_cache)

    def row(self, source, link, depth):
        """Return the links.csv row for a link, or None if it doesn't lead to Drive and has no note to check it."""
        target, short = self.target(link.url)
        if not target and not link.note:
            return None
        return {
            **dataclasses.asdict(link),
            "source": source,
            "target_url": target or "",
            "short_url": short,
            "item_id": self.item_id(target) if target else "",
            "resource_key": resource_key(target) if target else "",
            "url_status": "",
            "http": "",
            "depth": depth,
        }

    # Sources

    def source_links(self):
        """Return the links.csv rows of the sources' links, checked, with each source's count."""
        context = Context(
            extractor=self.extractor,
            cache_dir=self.config.cache_dir,
            max_text_bytes=self.config.walk.max_text_kb * 1000,
            session=self.session,
            google=self.google,
        )
        kept = []
        if self.only:
            kept = [
                r for r in read_csv(self.path("links.csv")) if r["source"] not in self.only and r["source"] != WALK
            ]
        found = []
        for options in self.config.sources:
            source = create(context, options)
            self.also_follow |= source.follow
            if self.only and source.name not in self.only:
                continue
            logger.info("reading source %s", source.name)
            found += [(source.name, link, 0) for link in source.links()]
            if n := getattr(source, "skipped_for_size", 0):
                self.result.skipped_for_size[source.name] = n
        self.resolve(found)
        rows = [r for args in found if (r := self.row(*args))]
        # Each URL is checked as found, with only the resource key it carries.
        results = self.checker.check_all(r["target_url"] for r in rows if r["target_url"])
        for r in rows:
            if r["target_url"]:
                r["url_status"] = results[r["target_url"]]["status"]
                r["http"] = results[r["target_url"]]["http"]
        linked = {(r["source"], r["item_id"]) for r in rows + kept if r["part"] != "bare_id"}
        # Most strings that look like IDs aren't, so Drive must find them. One its source links by URL is redundant.
        rows = [
            r
            for r in rows
            if r["part"] != "bare_id" or (r["url_status"] in FOUND and (r["source"], r["item_id"]) not in linked)
        ]
        self.result.rate_limited += sum(1 for r in rows if r["url_status"] == "rate_limited")
        return kept + rows

    # Items

    def inspect(self, item_id, key, url):
        """Return what's known of an item: its metadata, link role, anonymous check, and who can read it."""
        m = {
            "item_id": item_id,
            "link_role": "?",
            "read_as": "",
            "mime_type": kind(url),
            "error": "",
            "resource_key": key,
        }
        if is_published(item_id):
            # A published file has no Drive ID to read its metadata or permissions by. Its page is public or not.
            result = self.checker.check(published_url(item_id, m["mime_type"]))
            m.update(status=result["status"], http=result["http"], checked_at=result["checked_at"])
            return m
        if self.google.credentialed:
            user, info = self.google.metadata(item_id, key)
            if info:
                owner = (info.get("owners") or [{}])[0].get("emailAddress", "")
                m.update(
                    name=info.get("name", ""),
                    mime_type=info.get("mimeType", ""),
                    drive_id=info.get("driveId", ""),
                    owner=owner,
                    modified=info.get("modifiedTime", ""),
                    size=info.get("size", ""),
                    read_as=user,
                    version=info.get("md5Checksum") or info.get("headRevisionId") or info.get("modifiedTime", ""),
                    resource_key=info.get("resourceKey") or key,
                    description=info.get("description", ""),
                )
                permission_reader = self.reader(m["drive_id"], owner) or user
                m["link_role"], inherited, m["link_from"] = self.google.link(permission_reader, item_id)
                m["link_inherited"] = "1" if inherited else ""
            else:
                m["error"] = "no access"
        if not self.google.credentialed or self.config.check.items == "all" or m["link_role"] == "?":
            result = self.checker.check(item_check_url(item_id, m["mime_type"]))
            m.update(status=result["status"], http=result["http"], checked_at=result["checked_at"])
            if not m["mime_type"]:
                m["mime_type"] = kind(result.get("location", ""))
        return m

    def own(self, m):
        """Return whether the organization owns the item: in a shared drive in drive_readers, or owned in domain."""
        if m.get("drive_id"):
            return m["drive_id"] in self.drive_readers
        domain = self.google.domain
        return bool(domain and m.get("owner", "").lower().endswith("@" + domain))

    def within_outside_hops(self, m):
        limit = self.config.walk.max_outside_hops
        # Without credentials, owners are unknown.
        return limit is None or not self.google.credentialed or int(m["outside_hops"]) <= limit

    def followed(self, item_id, m):
        if item_id in self.also_follow:
            return True
        if m["link_role"] not in ("", "?"):
            return True
        return m["link_role"] == "?" and opens(m.get("status", ""))

    # Scans

    def scan(self, item_id, m, depth):
        """Return (links.csv rows, scans.csv rows, comments.csv rows) for a followed item."""
        mime = m["mime_type"]
        user = m["read_as"] or None
        # An item no subject can open is read anonymously if it opens.
        if not user and self.google.credentialed and not opens(m.get("status", "")):
            return [], [], []
        location = location_of(item_id, mime)
        key = m.get("resource_key") or ""
        links, scans, comments = [], [], []

        def add(found, channel, error="", note="", read_as=None, *, read=True):
            found = [dataclasses.replace(link, location=location, channel=channel) for link in found]
            scans.append(
                {
                    "item_id": item_id,
                    "channel": channel,
                    "read_as": read_as or user or ANONYMOUS,
                    "ok": "1" if read and not error else "",
                    "error": error,
                    "links": len(found),
                    "note": note,
                }
            )
            links.extend(found)

        if user and m.get("description") and "metadata" in self.channels:
            add(self.extractor.text(m["description"], part="description"), "metadata")

        if is_published(item_id):
            if "export" in self.channels:
                found, error, note = self.page_links(item_id, location, mime)
                add(found, "export", error, note, ANONYMOUS)
        elif mime in g.EXPORTS:
            if "export" in self.channels:
                with self.google.export(user, item_id, mime, key) as (path, error):
                    found, read_error = self.extractor.file(path) if path else ([], "")
                add(found, "export", error or read_error)
            if user and "api" in self.channels and mime in g.API_TYPES:
                found, error = self.api_links(user, item_id, mime)
                add(found, "api", error)
            if user and "comments" in self.channels:
                found, comment_rows, error = self.comment_links(user, item_id, mime, key)
                comments += comment_rows
                add(found, "comments", error)
        elif mime == FORM:
            # A form's public page is what respondents see, if it accepts responses.
            if "export" in self.channels:
                found, error, note = self.page_links(item_id, item_check_url(item_id, mime), mime)
                add(found, "export", error, note, ANONYMOUS)
            if user and "api" in self.channels:
                found, error = self.form_links(user, item_id)
                add(found, "api", error)
        elif mime == FOLDER:
            if self.folder_limit == 0:
                pass
            elif not user:
                add([], "listing", "not walked: a folder can be listed only with credentials")
            else:
                found, error, note = self.folder_links(user, item_id)
                add(found, "listing", error, note)
        elif mime and g.is_google(mime):
            add([], "none", note=f"not read: {mime}", read=False)
        else:
            found, error, note = self.download_links(user, item_id, m, key)
            if found is None:
                add([], "none", note=note, read=False)
            else:
                add(found, "download", error, note)
        return [r for link in links if (r := self.row(WALK, link, depth))], scans, comments

    def page_links(self, item_id, url, mime):
        """Return (Links, error, note) from a form's or a published file's public page."""
        final, html, error = self.google.page(url)
        if html is None:
            return [], error, ""
        if signin(final):
            return [], "sign-in", ""
        if "/closedform" in final:
            return [], "", "not read: the form doesn't accept responses"
        if mime == FORM:
            texts = g.form_page_texts(html)
            if texts is None:
                return [], "no form data in the page", ""
            found = self.extractor.text("\n".join(texts), "html")
        else:
            found = self.extractor.text(html, "html")
        # A page links to itself, and a form's page has each text with and without its HTML.
        own = {item_id, PUBLISHED_PREFIX + (published_id(final) or "")}
        best = {}
        for link in found:
            if self.item_id(link.url) in own:
                continue
            if link.url not in best or (link.text and not best[link.url].text):
                best[link.url] = link
        return list(best.values()), "", ""

    def form_links(self, user, item_id):
        """Return (Links, error) from the Forms API. A form that doesn't accept responses shows no one its links."""
        form, error = self.google.form(user, item_id)
        if form is None:
            return [], error
        visibility = "visible" if g.accepting(form) else "hidden"
        found = []
        for container, text in g.form_texts(form):
            found += self.extractor.text(text, part="body", container=container, visibility=visibility)
        return [link for link in found if self.item_id(link.url) != item_id], ""

    def api_links(self, user, item_id, mime):
        """Return (Links, error) from the Docs, Sheets or Slides API."""
        if mime == g.DOCUMENT:
            doc, error = self.google.document(user, item_id)
            if doc is None:
                return [], error
            found = [
                Link("", url, part=part, container=title) for _, url, part, title in g.doc_links(doc, detail=True)
            ]
        elif mime == g.SPREADSHEET:
            sheet, error = self.google.spreadsheet(user, item_id)
            if sheet is None:
                return [], error
            found = []
            for _, url, part, place, visibility, text in g.sheet_links(sheet, detail=True):
                fields = {"part": part, "container": place, "visibility": visibility}
                if url:
                    found.append(Link("", url, **fields))
                else:
                    found += self.extractor.text(text, **fields)
        else:
            deck, error = self.google.presentation(user, item_id)
            if deck is None:
                return [], error
            found = [
                Link("", url, part=part, container=slide) for _, url, part, slide in g.slide_links(deck, detail=True)
            ]
        return [link for link in found if self.extractor.is_link(link.url)], ""

    def comment_links(self, user, item_id, mime, key):
        """Return (Links in comments and replies, comments.csv rows, error)."""
        items, error = self.google.comments(user, item_id)
        found, rows = [], []
        text = None
        if mime == g.DOCUMENT and any(not c.get("resolved") and c.get("quotedFileContent") for c in items):
            with self.google.export(user, item_id, mime, key, text=True) as (path, _):
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
                found += self.extractor.text(body, "html", part=part, container=container)
                author = entry.get("author", {})
                rows.append(
                    {
                        "item_id": item_id,
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

    def folder_links(self, user, item_id):
        """Return (Links to the folder's items, error, note)."""
        limit = self.folder_limit if self.folder_limit is not None else 10**9
        children, error = self.google.children(user, item_id, limit)
        if error:
            return [], error, ""
        if len(children) > limit:
            return [], "", f"not walked: more than {limit} items"
        found = []
        for child in children:
            url = location_of(child["id"], child.get("mimeType", ""))
            if child.get("resourceKey"):
                url += f"?resourcekey={child['resourceKey']}"
            found.append(Link("", url, child.get("name", ""), part="folder_child"))
        return found, "", ""

    def download_links(self, user, item_id, m, key):
        """Return (Links, error, note) for a non-Google file. If it isn't a type to read, Links is None."""
        mime, name = m["mime_type"], m.get("name", "")
        # A CSV can be uploaded with an Excel MIME type.
        csv_file = name.lower().endswith(".csv") or mime == "text/csv"
        if csv_file and not self.config.walk.download_csv:
            return None, "", "not read: a CSV file (see download_csv)"
        if user and not csv_file and mime not in g.DOWNLOADS:
            return None, "", f"not read: {mime or 'unknown type'}"
        if m.get("size") and int(m["size"]) > self.max_download:
            return [], "", f"skipped: larger than {self.config.walk.max_download_mb} MB"
        cached = self.downloads_cache.get(item_id)
        if cached and m.get("version") and cached.get("version") == m["version"]:
            return [Link(**link) for link in cached["links"]], cached.get("error", ""), "cached"
        ext = g.DOWNLOADS.get(mime, ".csv" if csv_file else "")
        with self.google.download(user, item_id, key, self.max_download, ext) as (path, error):
            if not path:
                note = "skipped: larger than" if error.startswith("larger than") else ""
                return [], error, note and f"{note} {self.config.walk.max_download_mb} MB"
            if not user and not self.sniff(path):
                return None, "", "not read: not a PDF, Office file or notebook"
            found, read_error = self.extractor.file(path)
        if m.get("version"):
            self.downloads_cache[item_id] = {
                "version": m["version"],
                "links": [dataclasses.asdict(link) for link in found],
                "error": read_error,
            }
        return found, read_error, ""

    def sniff(self, path):
        """Return whether an anonymous download is a type to read: a PDF, Office file or notebook."""
        with path.open("rb") as f:
            head = f.read(8)
        if head.startswith((b"%PDF-", b"PK\x03\x04", b"\xd0\xcf\x11\xe0")) or path.suffix == ".ipynb":
            return True
        return bool(self.config.walk.download_csv and path.suffix == ".csv")

    # The walk

    def walk(self, rows, state):
        """Walk from the items that the rows link to, level by level, writing the outputs after each level."""
        seen = state.get("seen") or {}
        keys = state.get("keys") or {}
        info = state.get("info") or {}
        urls = state.get("urls") or {}
        # The fewest outside files in a row on the way to each item, through its parents at the shortest depth.
        runs = state.get("runs") or {}
        level, depth = state.get("level"), state.get("depth", 0)
        if level is None:
            for r in rows:
                if r["item_id"]:
                    seen.setdefault(r["item_id"], [0, r["location"]])
                    urls.setdefault(r["item_id"], r["target_url"])
                    if r["resource_key"]:
                        keys.setdefault(r["item_id"], r["resource_key"])
            level = sorted(seen)
        walk_rows = [r for r in rows if r["source"] == WALK]
        rows = [r for r in rows if r["source"] != WALK]
        scans = read_csv(self.path("scans.csv")) if self.resume else []
        comments = read_csv(self.path("comments.csv")) if self.resume else []
        max_depth = self.config.walk.max_depth
        while level and depth <= max_depth:
            with ThreadPoolExecutor(self.config.walk.workers) as pool:
                metas = list(pool.map(lambda i: self.inspect(i, keys.get(i, ""), urls.get(i, "")), level))
            to_scan = []
            for i, m in zip(level, metas, strict=True):
                m["followed"] = "1" if self.followed(i, m) else ""
                m["outside_hops"] = 0 if self.own(m) else runs.get(i, 0) + 1
                info[i] = m
                if m["followed"] and depth < max_depth:
                    if self.within_outside_hops(m):
                        to_scan.append(i)
                    else:
                        self.result.skipped_for_outside_hops += 1
            if depth == max_depth and (unfollowed := [i for i in level if info[i]["followed"]]):
                logger.warning("depth limit %d reached: %d followed items not read", max_depth, len(unfollowed))
            with ThreadPoolExecutor(self.config.walk.workers) as pool:
                results = list(pool.map(lambda i, d=depth: self.scan(i, info[i], d), to_scan))
            nxt = []
            for i, (found, scan_rows, comment_rows) in zip(to_scan, results, strict=True):
                walk_rows += found
                scans = [s for s in scans if s["item_id"] != i] + scan_rows
                comments = [c for c in comments if c["item_id"] != i] + comment_rows
                for r in found:
                    j = r["item_id"]
                    if j and r["resource_key"]:
                        keys.setdefault(j, r["resource_key"])
                    if j and j != i and j not in seen:
                        seen[j] = [depth + 1, i]
                        urls[j] = r["target_url"]
                        runs[j] = info[i]["outside_hops"]
                        nxt.append(j)
                    elif j and seen.get(j, [None])[0] == depth + 1:
                        runs[j] = min(runs.get(j, 0), info[i]["outside_hops"])
            level, depth = nxt, depth + 1
            state = {
                "seen": seen,
                "keys": keys,
                "info": info,
                "urls": urls,
                "runs": runs,
                "level": level,
                "depth": depth,
            }
            self.write(rows + walk_rows, seen, info, scans, comments, state)
            logger.info("depth %d: %d items, %d read, %d new", depth - 1, len(metas), len(to_scan), len(nxt))
        return rows + walk_rows, seen, info, scans, comments

    def write(self, rows, seen, info, scans, comments, state):
        items = [
            {**info[i], "item_id": i, "depth": d, "linked_from": src}
            for i, (d, src) in sorted(seen.items(), key=lambda kv: (kv[1][0], kv[0]))
            if i in info
        ]
        write_csv(self.path("links.csv"), LINK_FIELDS, rows)
        write_csv(self.path("items.csv"), ITEM_FIELDS, items)
        write_csv(self.path("scans.csv"), SCAN_FIELDS, scans)
        write_csv(self.path("comments.csv"), COMMENT_FIELDS, comments)
        write_json(self.path("state.json"), state)
        write_json(self.downloads_cache_path, self.downloads_cache)
        self.checker.save()
        self.shortlinks.save(self.shortlinks_cache)

    def execute(self):
        started = now()
        state = read_json(self.path("state.json"), {}) if self.resume else {}
        if self.resume and state:
            rows = read_csv(self.path("links.csv"))
        else:
            rows = self.source_links()
            state = {}
        if self.walk_enabled:
            rows, seen, info, scans, comments = self.walk(rows, state)
        else:
            seen, info, scans, comments = {}, {}, [], []
        self.write(rows, seen, info, scans, comments, {})
        skipped = sum(1 for s in scans if s.get("note", "").startswith("skipped: larger than"))
        if skipped:
            self.result.skipped_for_size[WALK] = skipped
        self.result.rate_limited += sum(1 for m in info.values() if m.get("status") == "rate_limited")
        self.result.links = len(rows)
        self.result.items = len(seen)
        counts = {}
        for r in rows:
            counts[r["source"]] = counts.get(r["source"], 0) + 1
        previous = read_json(self.path("run.json"), {})
        names = [s.get("name") or s["type"] for s in self.config.sources]
        write_json(
            self.path("run.json"),
            {
                "started": previous.get("started", started) if self.only else started,
                "finished": now(),
                "sources": names,
                "complete": True,
                "links": counts,
                "items": len(seen),
                "rate_limited": self.result.rate_limited,
                "skipped_for_size": self.result.skipped_for_size,
                "skipped_for_outside_hops": self.result.skipped_for_outside_hops,
                "config": dataclasses.asdict(self.config),
                "refreshed": sorted(self.only),
            },
        )
        self.path("state.json").unlink(missing_ok=True)
        return self.result


def run(config, out, **kwargs):
    """Run the sources and walk from their links into ``out``. Return a Result."""
    return Run(config, out, **kwargs).execute()
