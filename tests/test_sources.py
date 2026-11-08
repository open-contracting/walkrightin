import subprocess

import pytest

from walkrightin.sources import Context, create
from walkrightin.sources.wordpress import WordPressSource

from .conftest import DOC, FILE

DOC_URL = f"https://docs.google.com/document/d/{DOC}/edit"
FILE_URL = f"https://drive.google.com/file/d/{FILE}/view"
ESCAPED_DOC_URL = DOC_URL.replace("/", "\\/")


@pytest.fixture
def context(extractor, tmp_path):
    return Context(extractor=extractor, cache_dir=str(tmp_path / "cache"), max_text_bytes=1000, session=None)


def test_create_unknown_type(context):
    with pytest.raises(ValueError, match="unknown source type 'nope'"):
        create(context, {"type": "nope"})


def test_create_unknown_option(context):
    with pytest.raises(ValueError, match=r"unknown option\(s\) for source 'csv': colour"):
        create(context, {"type": "csv", "path": "x.csv", "colour": "red"})


def test_files(context, tmp_path):
    root = tmp_path / "uploads"
    (root / "2020").mkdir(parents=True)
    (root / "2020" / "list.csv").write_text(f"name,url\nx,{DOC_URL}\n")
    (root / "image.png").write_bytes(b"\x89PNG")

    source = create(context, {"type": "files", "path": str(root), "location_prefix": "wp-content/uploads/"})
    links = list(source.links())

    assert [(link.location, link.url) for link in links] == [("wp-content/uploads/2020/list.csv", DOC_URL)]
    assert source.skipped == {".png": 1}


def test_csv(context, tmp_path):
    path = tmp_path / "export.csv"
    path.write_text(f"file,page URL,link text\n{DOC_URL},https://a.org/x | https://a.org/y,Doc\n")

    source = create(
        context,
        {
            "type": "csv",
            "path": str(path),
            "url": "file",
            "location": "page URL",
            "text": "link text",
            "separator": " | ",
        },
    )

    assert [(link.location, link.url, link.text) for link in source.links()] == [
        ("https://a.org/x", DOC_URL, "Doc"),
        ("https://a.org/y", DOC_URL, "Doc"),
    ]


def test_drive(context):
    source = create(context, {"type": "drive", "ids": [DOC, DOC]})

    assert [(link.location, link.url) for link in source.links()] == [
        ("drive:ids", f"https://drive.google.com/file/d/{DOC}/view")
    ]
    assert source.follow == {DOC}
    assert create(context, {"type": "drive", "ids": [DOC], "follow": False}).follow == set()


def test_github_files(context, tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "README.md").write_text(f"[Doc]({DOC_URL})\n")
    (repo / "big.txt").write_text(DOC_URL + " " * 2000)
    (repo / "code.py").write_text(f'ID = "{FILE[:33]}"\n')
    (repo / "data.bin").write_bytes(b"\0" + DOC_URL.encode())
    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    subprocess.run(["git", "-C", str(repo), "add", "."], check=True)

    source = create(context, {"type": "github", "repos": ["o/r"], "bare_ids": True})
    files = {path: [(link.url, link.text, link.part) for link in found] for path, found in source.files(str(repo))}

    assert files == {
        "README.md": [(DOC_URL, "Doc", "body")],
        "code.py": [(f"https://drive.google.com/file/d/{FILE[:33]}/view", "", "bare_id")],
    }
    assert source.skipped_for_size == 1


def test_wordpress(context, monkeypatch):
    rows = [
        (1, "post_content", f'<p><a href="{DOC_URL}">Doc</a></p>'),
        (2, "acf_link", f's:5:"title";s:6:"Report";s:3:"url";s:{len(FILE_URL)}:"{FILE_URL}";'),
        (3, "block", f'{{"title":"Same","url":"{ESCAPED_DOC_URL}"}}'),
        (
            4,
            "post_content",
            (
                f'{{"link":{{"title":"Deep dive","url":"{ESCAPED_DOC_URL}"}},'
                f'"buttons_0_link":{{"title":"VIEW \\u00e1","url":"{ESCAPED_DOC_URL}"}}}}'
            ),
        ),
    ]
    monkeypatch.setattr(WordPressSource, "rows", lambda _self: iter(rows))

    source = create(context, {"type": "wordpress", "database": "wp"})

    assert [(link.location, link.url, link.text) for link in source.links()] == [
        ("wp:1/post_content", DOC_URL, "Doc"),
        ("wp:2/acf_link", FILE_URL, "Report"),
        ("wp:3/block", DOC_URL, "Same"),
        ("wp:4/post_content", DOC_URL, "Deep dive | VIEW á"),
    ]
