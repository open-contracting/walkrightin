import csv
import re

import pytest

from walkrightin import config as configuration
from walkrightin import platforms
from walkrightin.check import Checker
from walkrightin.extract import Extractor
from walkrightin.model import Link
from walkrightin.platforms import Platform, Scan, join_key, split_key
from walkrightin.platforms.google_drive import GoogleDrive
from walkrightin.walk import Run

from .conftest import DOC, FakeShortLinks

DOC_URL = f"https://docs.google.com/document/d/{DOC}/edit"
BOX = "https://box.example.net/i/abc123"
CHILD = "https://box.example.net/i/def456"


class Box(Platform):
    """A made-up platform, whose items are boxes that hold links and other boxes."""

    name = "box"
    pattern = r"https?://box\.example\.net/"
    hosts = r"box\.example\."

    def key(self, url):
        m = re.search(r"/i/(\w+)", url)
        return (m.group(1) if m else ""), ""

    def url(self, item_id):
        return f"https://box.example.net/i/{item_id}"

    def redirect_status(self, location):
        return "signin" if "/login" in location else None

    def item_check_url(self, item_id, meta):
        return self.url(item_id)

    def location(self, item_id, meta):
        return self.url(item_id)

    def read(self, item_id, meta):
        scans = [Scan("export", [Link("", DOC_URL, "Doc")])]
        if scan := self.listing(lambda _limit: ([Link("", CHILD, "Child", part="folder_child")], "")):
            scans.append(scan)
        return scans


@pytest.fixture
def installed(monkeypatch):
    classes = {"box": Box, "google_drive": GoogleDrive}
    monkeypatch.setattr(platforms, "registry", lambda: classes)
    monkeypatch.setattr(configuration, "registry", lambda: classes)
    return platforms.load()


def test_keys():
    assert join_key("box", "e:x") == "box:e:x"
    assert split_key("box:e:x") == ("box", "e:x")


def test_recognize(installed):
    extractor = Extractor(FakeShortLinks({"https://bit.ly/box": BOX}, platforms=installed))

    links = extractor.text(f"See {BOX}, https://box.example.net/about and bit.ly/box and {DOC_URL}")

    assert [link.url for link in links] == [BOX, "https://box.example.net/about", DOC_URL, "https://bit.ly/box"]
    assert installed.key(BOX) == ("box:abc123", "")
    assert installed.key(DOC_URL) == (f"google_drive:{DOC}", "")
    assert installed.key("https://example.com/") == ("", "")
    assert extractor.shortlinks.resolve("https://bit.ly/box") == BOX


def test_check(installed, monkeypatch):
    checker = Checker(delay=0, platforms=installed)
    login = "https://box.example.net/login"
    responses = {BOX: (302, login), DOC_URL: (302, login), login: (200, "")}

    def get(url):
        code, location = responses[url]
        headers = {"Location": location} if location else {}
        return type("Response", (), {"status_code": code, "headers": headers, "is_redirect": code == 302})

    monkeypatch.setattr(checker, "get", get)

    # Each URL's redirects mean what its own platform says.
    assert checker.request(BOX)["status"] == "signin"
    assert checker.request(DOC_URL)["status"] == "redirect"


def test_walk(installed, tmp_path):
    seeds = tmp_path / "seeds.csv"
    seeds.write_text(f"url,location\n{BOX},page\n")
    data = {
        "cache_dir": str(tmp_path / "cache"),
        "walk": {"max_depth": 1, "max_folder_children": 5},
        "check": {"delay": 0},
        "sources": [{"type": "csv", "name": "seeds", "path": str(seeds)}],
    }
    run = Run(configuration.load(data=data), str(tmp_path / "out"))
    run.checker.check = lambda _url: {"status": "public", "http": "200", "checked_at": "now", "location": ""}

    run.execute()

    with (tmp_path / "out" / "items.csv").open() as f:
        items = {r["item_id"]: r for r in csv.DictReader(f)}
    assert {i: (r["depth"], r["linked_from"], r["followed"]) for i, r in items.items()} == {
        "box:abc123": ("0", "page", "1"),
        f"google_drive:{DOC}": ("1", "box:abc123", "1"),
        "box:def456": ("1", "box:abc123", "1"),
    }
    with (tmp_path / "out" / "scans.csv").open() as f:
        scans = [(r["item_id"], r["channel"], r["ok"], r["links"], r["note"]) for r in csv.DictReader(f)]
    assert scans == [("box:abc123", "export", "1", "1", ""), ("box:abc123", "listing", "1", "1", "")]
