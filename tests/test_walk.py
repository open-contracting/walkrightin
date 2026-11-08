import csv
import json
import tempfile
from contextlib import contextmanager
from pathlib import Path

import pytest

from walkrightin import config as configuration
from walkrightin.model import Link
from walkrightin.walk import Run

from .conftest import DOC, FILE, SHEET, FakeShortLinks, needs_pdf_tools, pdf

FOLDER_ID = "1FoLdErFoLdErFoLdErFoLdErFoLdEr12"
CHILD = "1ChIlDcHiLdChIlDcHiLdChIlDcHiLd34"
DOC_URL = f"https://docs.google.com/document/d/{DOC}/edit"
FILE_URL = f"https://drive.google.com/file/d/{FILE}/view"
SHEET_URL = f"https://docs.google.com/spreadsheets/d/{SHEET}/edit"
FOLDER_URL = f"https://drive.google.com/drive/folders/{FOLDER_ID}"
DOCUMENT = "application/vnd.google-apps.document"
FORM = "application/vnd.google-apps.form"
FORM_ID = "1FoRmFoRmFoRmFoRmFoRmFoRmFoRmFoRmFoRmFoRmFo"
PUBLISHED = "2PACX-1vQpUbLiShEdpUbLiShEdpUbLiShEd"
SPREADSHEET = "application/vnd.google-apps.spreadsheet"
FOLDER = "application/vnd.google-apps.folder"


class FakeGoogle:
    def __init__(
        self,
        *,
        credentialed=False,
        files=None,
        roles=None,
        contents=None,
        children=None,
        comments=None,
        pages=None,
        forms=None,
    ):
        self.credentialed = credentialed
        self.subjects = ["staff@example.org"] if credentialed else []
        self.domain = "example.org"
        self.files = files or {}
        self.roles = roles or {}
        self.contents = contents or {}
        self.folders = children or {}
        self.comment_lists = comments or {}
        self.pages = pages or {}
        self.forms = forms or {}
        self.exported_as = []
        self.read_as = []
        self.inherited = {}

    def users_for(self, owner=""):
        return self.subjects

    def metadata(self, file_id, key="", users=None):
        if self.credentialed and file_id in self.files:
            return "staff@example.org", {"id": file_id, **self.files[file_id]}
        return None, None

    def link(self, user, file_id):
        self.read_as.append((user, file_id))
        return self.roles.get(file_id, "?"), file_id in self.inherited, self.inherited.get(file_id, "")

    @contextmanager
    def export(self, user, file_id, mime_type, key="", *, text=False):
        self.exported_as.append((file_id, user, text))
        if file_id not in self.contents:
            yield None, "403 Forbidden"
            return
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / f"{file_id}.{'txt' if text else 'html'}"
            path.write_text(self.contents[file_id + (":txt" if text else "")] if text else self.contents[file_id])
            yield path, ""

    @contextmanager
    def download(self, user, file_id, key="", max_bytes=None, ext=""):
        yield None, "not here"

    def children(self, user, folder_id, limit):
        return self.folders.get(folder_id, [])[: limit + 1], ""

    def comments(self, user, file_id):
        return self.comment_lists.get(file_id, []), ""

    def document(self, user, doc_id):
        return None, "no API"

    def spreadsheet(self, user, sheet_id):
        return None, "no API"

    def presentation(self, user, presentation_id):
        return None, "no API"

    def form(self, user, form_id):
        return (self.forms[form_id], "") if form_id in self.forms else (None, "no API")

    def page(self, url):
        for part, (final, html) in self.pages.items():
            if part in url:
                return final, html, ""
        return url, None, "404 Not Found"


def make_run(tmp_path, google, statuses, sources=None, walk=None, check=None, google_config=None, **kwargs):
    seeds = tmp_path / "seeds.csv"
    if not seeds.exists():
        seeds.write_text(f"url,location\n{DOC_URL},https://example.org/page\n")
    data = {
        "cache_dir": str(tmp_path / "cache"),
        "walk": {"channels": ["export", "comments"], **(walk or {})},
        "check": {"delay": 0, **(check or {})},
        "sources": sources or [{"type": "csv", "name": "seeds", "path": str(seeds)}],
        "google": google_config or {},
    }
    walk_enabled = kwargs.pop("walk_enabled", True)
    run = Run(configuration.load(data=data), str(tmp_path / "out"), walk=walk_enabled, **kwargs)
    run.shortlinks = FakeShortLinks({"https://bit.ly/sheet": SHEET_URL})
    run.extractor.shortlinks = run.shortlinks
    run.google = google

    def check(url):
        status = next((s for i, s in statuses.items() if i in url), "not_found")
        return {"status": status, "http": "", "checked_at": "now", "location": ""}

    run.checker.check = check
    return run


def read(tmp_path, name):
    with (tmp_path / "out" / name).open() as f:
        return list(csv.DictReader(f))


def items(tmp_path):
    return {r["item_id"]: r for r in read(tmp_path, "items.csv")}


def test_anonymous(tmp_path):
    google = FakeGoogle(contents={DOC: f'<a href="{FILE_URL}">File</a> and <a href="https://bit.ly/sheet">Sheet</a>'})
    run = make_run(tmp_path, google, {DOC: "public", FILE: "signin", SHEET: "public"})

    result = run.execute()

    found = items(tmp_path)
    assert {i: (r["depth"], r["linked_from"], r["followed"], r["status"]) for i, r in found.items()} == {
        DOC: ("0", "https://example.org/page", "1", "public"),
        FILE: ("1", DOC, "", "signin"),
        SHEET: ("1", DOC, "1", "public"),
    }
    links = read(tmp_path, "links.csv")
    assert [(r["source"], r["item_id"], r["text"], r["short_url"], r["url_status"], r["depth"]) for r in links] == [
        ("seeds", DOC, "", "", "public", "0"),
        ("walk", FILE, "File", "", "", "0"),
        ("walk", SHEET, "Sheet", "https://bit.ly/sheet", "", "0"),
    ]
    assert links[1]["location"] == DOC_URL
    assert links[1]["channel"] == "export"
    scans = read(tmp_path, "scans.csv")
    assert [(r["item_id"], r["read_as"], r["links"]) for r in scans] == [
        (DOC, "anonymous", "2"),
        (SHEET, "anonymous", "0"),
    ]
    assert (result.links, result.items, result.rate_limited) == (3, 3, 0)
    run_json = json.loads((tmp_path / "out" / "run.json").read_text())
    assert run_json["complete"] is True
    assert run_json["links"] == {"seeds": 1, "walk": 2}
    assert not (tmp_path / "out" / "state.json").exists()


def test_depth_limit(tmp_path):
    google = FakeGoogle(contents={DOC: f'<a href="{SHEET_URL}">Sheet</a>', SHEET: f"{FILE_URL}"})
    make_run(tmp_path, google, {DOC: "public", SHEET: "public"}, walk={"max_depth": 1}).execute()

    found = items(tmp_path)
    assert set(found) == {DOC, SHEET}
    assert found[SHEET]["followed"] == "1"
    assert [r["item_id"] for r in read(tmp_path, "scans.csv")] == [DOC]


def credentialed(**kwargs):
    files = {
        DOC: {"name": "Doc", "mimeType": DOCUMENT, "owners": [{"emailAddress": "staff@example.org"}]},
        FOLDER_ID: {"name": "Folder", "mimeType": FOLDER},
        CHILD: {"name": "Child", "mimeType": SPREADSHEET},
        FILE: {"name": "File", "mimeType": "application/pdf"},
    }
    return FakeGoogle(
        credentialed=True,
        files=files,
        roles={DOC: "reader", FOLDER_ID: "reader", CHILD: "", FILE: ""},
        contents={DOC: f'<a href="{FOLDER_URL}">Folder</a>'},
        children={FOLDER_ID: [{"id": CHILD, "name": "Child", "mimeType": SPREADSHEET}, {"id": FILE, "name": "File"}]},
        **kwargs,
    )


@pytest.mark.parametrize(
    ("limit", "reached", "note"),
    [
        (0, {DOC, FOLDER_ID}, None),
        (5, {DOC, FOLDER_ID, CHILD, FILE}, ""),
        (1, {DOC, FOLDER_ID}, "not walked: more than 1 items"),
        ("all", {DOC, FOLDER_ID, CHILD, FILE}, ""),
    ],
)
def test_folders(tmp_path, limit, reached, note):
    run = make_run(tmp_path, credentialed(), {}, walk={"max_folder_children": limit}, check={"items": "unreadable"})

    run.execute()

    found = items(tmp_path)
    assert set(found) == reached
    assert found[DOC]["link_role"] == "reader"
    assert found[DOC]["read_as"] == "staff@example.org"
    # Readable roles aren't checked anonymously.
    assert found[DOC]["status"] == ""
    folder_scans = [r for r in read(tmp_path, "scans.csv") if r["item_id"] == FOLDER_ID]
    if note is None:
        assert folder_scans == []
    else:
        assert [r["note"] for r in folder_scans] == [note]
    if CHILD in reached:
        child = next(r for r in read(tmp_path, "links.csv") if r["item_id"] == CHILD)
        assert (child["part"], child["channel"], child["text"], child["location"]) == (
            "folder_child",
            "listing",
            "Child",
            FOLDER_URL,
        )
        assert found[CHILD]["depth"] == "2"
        assert found[CHILD]["followed"] == ""


def test_anonymous_fallback(tmp_path):
    google = credentialed()
    google.files.pop(DOC)
    google.contents[DOC] = f'<a href="{FILE_URL}">File</a>'
    run = make_run(tmp_path, google, {DOC: "public"}, walk={"max_folder_children": 0})

    run.execute()

    found = items(tmp_path)
    assert found[DOC]["error"] == "no access"
    assert found[DOC]["followed"] == "1"
    assert [(r["item_id"], r["read_as"]) for r in read(tmp_path, "scans.csv")] == [(DOC, "anonymous")]
    assert (DOC, None, False) in google.exported_as


def test_no_access_not_public(tmp_path):
    google = credentialed()
    google.files.pop(DOC)
    make_run(tmp_path, google, {DOC: "signin"}).execute()

    assert items(tmp_path)[DOC]["followed"] == ""
    assert read(tmp_path, "scans.csv") == []


def test_also_follow_and_readers(tmp_path):
    ids = tmp_path / "ids.txt"
    ids.write_text(f"{DOC}\n")
    readers = tmp_path / "drives.csv"
    readers.write_text("user,driveid,label\norganizer@example.org,0ADrive,internal\n")
    google = credentialed()
    google.roles[DOC] = ""
    google.files[DOC]["driveId"] = "0ADrive"
    google.files[FOLDER_ID]["driveId"] = "0AOther"
    google.inherited[FOLDER_ID] = "1ParentFolder"

    run = make_run(
        tmp_path,
        google,
        {},
        walk={"also_follow": str(ids), "max_folder_children": 0},
        google_config={"drive_readers": str(readers), "domain": "example.org"},
    )
    run.execute()

    found = items(tmp_path)
    assert found[DOC]["followed"] == "1"
    assert (found[FOLDER_ID]["link_inherited"], found[FOLDER_ID]["link_from"]) == ("1", "1ParentFolder")
    assert found[DOC]["link_inherited"] == ""
    # The drive's reader, else (for an unlisted drive) whoever can see the item.
    assert google.read_as[:2] == [("organizer@example.org", DOC), ("staff@example.org", FOLDER_ID)]


def test_comments(tmp_path):
    google = credentialed(
        comments={
            DOC: [
                {
                    "id": "c1",
                    "author": {"displayName": "Ana", "me": True},
                    "htmlContent": f'see <a href="{SHEET_URL}">this</a>',
                    "quotedFileContent": {"value": "Original   TEXT"},
                    "replies": [{"id": "r1", "author": {"displayName": "Bo"}, "content": FILE_URL}],
                },
                {"id": "c2", "author": {}, "resolved": True, "quotedFileContent": {"value": "gone"}},
            ]
        }
    )
    google.contents[f"{DOC}:txt"] = "Some original text here"
    make_run(tmp_path, google, {}, walk={"max_folder_children": 0}, check={"items": "unreadable"}).execute()

    links = {r["item_id"]: r for r in read(tmp_path, "links.csv") if r["channel"] == "comments"}
    assert (links[SHEET]["part"], links[SHEET]["container"], links[SHEET]["text"]) == ("comment", "c1", "this")
    assert links[FILE]["container"] == "c1/r1"
    comments = [
        (r["comment_id"], r["reply_id"], r["me"], r["resolved"], r["quote_present"])
        for r in read(tmp_path, "comments.csv")
    ]
    assert comments == [("c1", "", "1", "", "1"), ("c1", "r1", "", "", ""), ("c2", "", "", "1", "0")]


def test_bare_ids_verified(tmp_path):
    repo = tmp_path / "seeds.csv"
    # Another source's link to a bare ID doesn't make the bare ID redundant.
    repo.write_text(f"url,location\n{DOC_URL},page\nhttps://drive.google.com/file/d/{FILE[:33]}/view,page\n")
    found = tmp_path / "code.txt"
    found.write_text(
        f'a = "{FILE[:33]}"; b = "{SHEET[:33]}"; c = "https://drive.google.com/file/d/{DOC[:33]}/view {DOC[:33]}"'
    )
    sources = [
        {"type": "csv", "name": "seeds", "path": str(repo)},
        {"type": "files", "name": "code", "path": str(tmp_path), "extensions": [".txt"], "bare_ids": True},
    ]
    statuses = {DOC: "public", FILE[:33]: "signin", SHEET[:33]: "not_found", DOC[:33]: "public"}
    make_run(tmp_path, FakeGoogle(), statuses, sources=sources, walk={"max_depth": 0}).execute()

    bare = [r["item_id"] for r in read(tmp_path, "links.csv") if r["part"] == "bare_id"]
    assert bare == [FILE[:33]]


def test_refresh_one_source(tmp_path):
    other = tmp_path / "other.csv"
    other.write_text(f"url,location\n{FILE_URL},elsewhere\n")
    sources = [
        {"type": "csv", "name": "seeds", "path": str(tmp_path / "seeds.csv")},
        {"type": "csv", "name": "other", "path": str(other)},
    ]
    statuses = {DOC: "public", FILE: "public"}
    make_run(tmp_path, FakeGoogle(), statuses, sources=sources, walk={"max_depth": 0}).execute()
    other.write_text(f"url,location\n{SHEET_URL},elsewhere\n")

    make_run(tmp_path, FakeGoogle(), statuses, sources=sources, walk={"max_depth": 0}, only=["other"]).execute()

    assert [(r["source"], r["item_id"]) for r in read(tmp_path, "links.csv")] == [("seeds", DOC), ("other", SHEET)]
    assert json.loads((tmp_path / "out" / "run.json").read_text())["refreshed"] == ["other"]


def test_noted_link_without_target(tmp_path):
    (tmp_path / "seeds.csv").write_text("url,location\nhttps://example.com/split,page\n")
    run = make_run(tmp_path, FakeGoogle(), {}, walk_enabled=False)
    link = Link("page", "https://example.com/split", note="check by hand: 'https://' ends a line")

    assert run.row("files", link, 0)["target_url"] == ""
    assert run.row("files", Link("page", "https://example.com/other"), 0) is None


@needs_pdf_tools
def test_noted_link_in_run(tmp_path):
    uploads = tmp_path / "uploads"
    uploads.mkdir()
    (uploads / "a.pdf").write_bytes(pdf(["Visit https://", "example.com/page", "and read the guide.", DOC_URL]))
    sources = [{"type": "files", "name": "uploads", "path": str(uploads)}]

    result = make_run(tmp_path, FakeGoogle(), {DOC: "public"}, sources=sources, walk_enabled=False).execute()

    assert result.links == 2
    assert sorted((r["url_status"], r["note"][:13]) for r in read(tmp_path, "links.csv")) == [
        ("", "check by hand"),
        ("public", ""),
    ]


def test_no_walk(tmp_path):
    run = make_run(tmp_path, FakeGoogle(), {DOC: "public"}, walk_enabled=False)

    run.execute()

    assert [r["url_status"] for r in read(tmp_path, "links.csv")] == ["public"]
    assert read(tmp_path, "items.csv") == []


@pytest.mark.parametrize(("limit", "scanned", "reached"), [(None, 4, 4), (1, 2, 3), (0, 1, 2)])
def test_max_outside_hops(tmp_path, limit, scanned, reached):
    outside = [
        "1OuTsIdEaOuTsIdEaOuTsIdEaOuTsIdEa1",
        "1OuTsIdEbOuTsIdEbOuTsIdEbOuTsIdEb2",
        "1OuTsIdEcOuTsIdEcOuTsIdEcOuTsIdEc3",
    ]
    google = FakeGoogle(
        credentialed=True,
        files={
            DOC: {"name": "Own", "mimeType": DOCUMENT, "owners": [{"emailAddress": "staff@example.org"}]},
            **{
                i: {"name": i, "mimeType": DOCUMENT, "owners": [{"emailAddress": "someone@partner.org"}]}
                for i in outside
            },
        },
        roles=dict.fromkeys([DOC, *outside], "reader"),
        contents={
            DOC: f"https://docs.google.com/document/d/{outside[0]}/edit",
            outside[0]: f"https://docs.google.com/document/d/{outside[1]}/edit",
            outside[1]: f"https://docs.google.com/document/d/{outside[2]}/edit",
            outside[2]: "no links",
        },
    )
    walk = {"max_folder_children": 0, **({"max_outside_hops": limit} if limit is not None else {})}

    result = make_run(tmp_path, google, {}, walk=walk, check={"items": "unreadable"}).execute()

    found = items(tmp_path)
    assert [found[i]["outside_hops"] for i in (DOC, *outside) if i in found] == ["0", "1", "2", "3"][:reached]
    assert len({r["item_id"] for r in read(tmp_path, "scans.csv")}) == scanned
    assert len(found) == reached
    assert result.skipped_for_outside_hops == (1 if limit is not None else 0)


def test_description(tmp_path):
    google = credentialed()
    google.files[DOC]["description"] = f"Background: {SHEET_URL}"
    walk = {"channels": ["export", "metadata"], "max_folder_children": 0}
    make_run(tmp_path, google, {}, walk=walk, check={"items": "unreadable"}).execute()

    link = next(r for r in read(tmp_path, "links.csv") if r["item_id"] == SHEET)
    assert (link["channel"], link["part"], link["location"]) == ("metadata", "description", DOC_URL)
    assert [(r["item_id"], r["channel"]) for r in read(tmp_path, "scans.csv")] == [(DOC, "metadata"), (DOC, "export")]


def form_page(*texts):
    data = json.dumps([None, [texts[0], [[1, text, None, 0] for text in texts[1:]]], "/forms"])
    return f"<html><script>var FB_PUBLIC_LOAD_DATA_ = {data};</script></html>"


@pytest.mark.parametrize(
    ("form", "visibility"),
    [({"publishSettings": {"publishState": {"isPublished": True}}}, "hidden"), ({}, "visible")],
)
def test_form(tmp_path, form, visibility):
    form_url = f"https://docs.google.com/forms/d/{FORM_ID}/edit"
    (tmp_path / "seeds.csv").write_text(f"url,location\n{form_url},page\n")
    google = FakeGoogle(
        credentialed=True,
        files={FORM_ID: {"name": "Form", "mimeType": FORM}},
        roles={FORM_ID: "reader"},
        pages={
            FORM_ID: (
                f"https://docs.google.com/forms/d/e/{PUBLISHED}/viewform",
                form_page(
                    f'Read <a href="{DOC_URL}">the guide</a>',
                    f"Read {DOC_URL}",
                    f"https://docs.google.com/forms/d/e/{PUBLISHED}/viewform",
                ),
            )
        },
        forms={
            FORM_ID: {
                "formId": FORM_ID,
                "responderUri": f"https://docs.google.com/forms/d/e/{PUBLISHED}/viewform",
                "info": {"title": "Form", "description": "Intro"},
                "items": [{"itemId": "q1", "title": "Upload", "description": f"Template: {FILE_URL}"}],
                **form,
            }
        },
    )
    make_run(tmp_path, google, {}, walk={"channels": ["export", "api"]}, check={"items": "unreadable"}).execute()

    links = [r for r in read(tmp_path, "links.csv") if r["source"] == "walk"]
    assert [(r["channel"], r["item_id"], r["text"], r["container"], r["visibility"]) for r in links] == [
        ("export", DOC, "the guide", "", "visible"),
        ("api", FILE, "", "q1", visibility),
    ]
    assert [(r["channel"], r["read_as"]) for r in read(tmp_path, "scans.csv")] == [
        ("export", "anonymous"),
        ("api", "staff@example.org"),
    ]


def test_closed_form(tmp_path):
    (tmp_path / "seeds.csv").write_text(f"url,location\nhttps://docs.google.com/forms/d/{FORM_ID}/edit,page\n")
    google = FakeGoogle(
        credentialed=True,
        files={FORM_ID: {"name": "Form", "mimeType": FORM}},
        roles={FORM_ID: "reader"},
        pages={FORM_ID: (f"https://docs.google.com/forms/d/e/{PUBLISHED}/closedform", "<html></html>")},
    )
    make_run(tmp_path, google, {}, walk={"channels": ["export"]}, check={"items": "unreadable"}).execute()

    assert [(r["ok"], r["note"]) for r in read(tmp_path, "scans.csv")] == [
        ("1", "not read: the form doesn't accept responses")
    ]


def test_published(tmp_path):
    url = f"https://docs.google.com/spreadsheets/d/e/{PUBLISHED}/pub?gid=0&single=true&output=csv"
    (tmp_path / "seeds.csv").write_text(f"url,location\n{url},page\n")
    page = f"https://docs.google.com/spreadsheets/d/e/{PUBLISHED}/pubhtml"
    google = FakeGoogle(
        credentialed=True,
        files={DOC: {"name": "Doc", "mimeType": DOCUMENT}},
        roles={DOC: ""},
        pages={page: (page, f'<a href="{page}?gid=1">Tab</a> <a href="{DOC_URL}">Doc</a>')},
    )
    make_run(tmp_path, google, {PUBLISHED: "public"}, check={"items": "unreadable"}).execute()

    found = items(tmp_path)
    item = "e:" + PUBLISHED
    assert (found[item]["status"], found[item]["followed"], found[item]["mime_type"]) == (
        "public",
        "1",
        SPREADSHEET,
    )
    assert found[DOC]["linked_from"] == item
    link = next(r for r in read(tmp_path, "links.csv") if r["source"] == "walk")
    assert (link["item_id"], link["location"], link["channel"]) == (DOC, page, "export")
    assert [(r["item_id"], r["read_as"]) for r in read(tmp_path, "scans.csv")] == [(item, "anonymous")]


def test_not_read(tmp_path):
    image = "1ImAgEiMaGeImAgEiMaGeImAgEiMaGe56"
    google = credentialed()
    google.files[image] = {"name": "Photo", "mimeType": "image/png"}
    google.files["1MaPmApMaPmApMaPmApMaPmApMaPmAp78"] = {"name": "Map", "mimeType": "application/vnd.google-apps.map"}
    google.roles.update(dict.fromkeys([image, "1MaPmApMaPmApMaPmApMaPmApMaPmAp78"], "reader"))
    google.contents[DOC] = (
        f"{FILE_URL.replace(FILE, image)} https://drive.google.com/open?id=1MaPmApMaPmApMaPmApMaPmApMaPmAp78"
    )
    make_run(tmp_path, google, {}, walk={"max_folder_children": 0}, check={"items": "unreadable"}).execute()

    scans = {r["item_id"]: r for r in read(tmp_path, "scans.csv") if r["channel"] == "none"}
    assert {i: (r["ok"], r["note"]) for i, r in scans.items()} == {
        image: ("", "not read: image/png"),
        "1MaPmApMaPmApMaPmApMaPmApMaPmAp78": ("", "not read: application/vnd.google-apps.map"),
    }


def test_drawing(tmp_path):
    drawing = "1DrAwInGdRaWiNgDrAwInGdRaWiNg9012"
    (tmp_path / "seeds.csv").write_text(f"url,location\nhttps://docs.google.com/drawings/d/{drawing}/edit,page\n")
    google = FakeGoogle(
        credentialed=True,
        files={drawing: {"name": "Drawing", "mimeType": "application/vnd.google-apps.drawing"}},
        roles={drawing: "reader"},
        contents={drawing: DOC_URL},
    )
    make_run(tmp_path, google, {}, walk={"channels": ["export", "api"], "max_depth": 1}).execute()

    assert [(r["channel"], r["links"]) for r in read(tmp_path, "scans.csv")] == [("export", "1")]
