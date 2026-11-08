import pytest

from walkrightin import urls
from walkrightin.urls import strip as strip_url

from .conftest import DOC


@pytest.mark.parametrize(
    ("url", "expected"),
    [
        (f"https://docs.google.com/document/d/{DOC}/edit", DOC),
        (f"https://drive.google.com/open?id={DOC}", DOC),
        (f"https://drive.google.com/drive/folders/{DOC}?usp=sharing", DOC),
        # A shared drive's ID is shorter.
        ("https://drive.google.com/drive/folders/0AFwZ8p5bAbCdUk9PVA", None),
        ("https://docs.google.com/document/d/e/2PACX-1vQabcdefghijklmnopqrstuvwxyz/pub", None),
    ],
)
def test_drive_id(url, expected):
    assert urls.drive_id(url) == expected


def test_published_id():
    assert urls.published_id("https://docs.google.com/forms/d/e/1FAIpQLSabcdefghij/viewform") == "1FAIpQLSabcdefghij"


def test_resource_key():
    assert urls.resource_key(f"https://drive.google.com/file/d/{DOC}/view?resourcekey=0-abc_DEF") == "0-abc_DEF"
    assert urls.resource_key(f"https://drive.google.com/file/d/{DOC}/view") == ""


@pytest.mark.parametrize(
    ("url", "expected"),
    [
        ("https://docs.google.com/document/d/", False),
        ("https://docs.google.com/document/d/$", False),
        ("https://docs.google.com/spreadsheets/d/e/", False),
        ("https://drive.google.com/open?id=", False),
        ("https://docs.google.com/drawings/", False),
        ("https://drive.google.com/drive/my-drive", False),
        (f"https://docs.google.com/document/d/{DOC}/edit", True),
        ("https://docs.google.com/spreadsheet/ccc?key=0AqLP9fZSKM8jdFg2WGhHQi1QbE54Wml5aDNyaUVDRGc", True),
        ("https://drive.google.com/drive/folders/0AFwZ8p5bAbCdUk9PVA", True),
    ],
)
def test_is_drive_link(url, expected):
    assert urls.is_drive_link(url) is expected


def test_bare_ids():
    text = f'id = "{DOC[:33]}"; font = "1111111111111111111111111111111111"; lower = "1{"a" * 32}"'
    assert urls.bare_ids(text) == {DOC[:33]}


@pytest.mark.parametrize(
    ("url", "expected"),
    [
        ("https://www.google.com/url?q=https://example.com/x&sa=D", "https://example.com/x"),
        (
            "https://eur01.safelinks.protection.outlook.com/?url=https%3A%2F%2Fexample.com&data=1",
            "https://example.com",
        ),
        ("https://l.facebook.com/l.php?u=https%3A%2F%2Fexample.com", "https://example.com"),
        ("https://www.google.com/search?q=x", "https://www.google.com/search?q=x"),
    ],
)
def test_unwrap(url, expected):
    assert urls.unwrap(url) == expected


def test_strip():
    assert strip_url("https://example.com/a&amp;b\u200b.") == "https://example.com/a&b"
    assert strip_url("https://example.com/a&quot;,&quot;b") == "https://example.com/a"


def test_check_url():
    assert urls.check_url(f"https://docs.google.com/document/d/{DOC}/copy") == (
        f"https://docs.google.com/document/d/{DOC}/edit"
    )
    assert urls.check_url(f"https://docs.google.com/document/d/{DOC}/copy?usp=sharing").endswith("/edit?usp=sharing")


def test_kind():
    assert urls.kind(f"https://docs.google.com/spreadsheets/d/{DOC}/edit") == urls.GOOGLE_TYPES["spreadsheets"]
    assert urls.kind(f"https://drive.google.com/drive/folders/{DOC}") == urls.FOLDER
    assert urls.kind(f"https://drive.google.com/file/d/{DOC}/view") == ""


def test_item_check_url():
    assert urls.item_check_url(DOC, urls.FORM).endswith("/viewform")
    assert urls.item_check_url(DOC, urls.FOLDER) == f"https://drive.google.com/drive/folders/{DOC}"
    assert urls.item_check_url(DOC, "") == f"https://drive.google.com/file/d/{DOC}/view"
