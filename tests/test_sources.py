import subprocess
from types import SimpleNamespace

import pytest

from walkrightin.sources import Context, create
from walkrightin.sources.github import GitHubSource
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


@pytest.mark.parametrize("names", [[], ["image.png"]])
def test_files_none(context, tmp_path, names):
    for name in names:
        (tmp_path / name).write_bytes(b"")
    source = create(context, {"type": "files", "path": str(tmp_path), "extensions": [".pdf"]})

    with pytest.raises(FileNotFoundError, match=r"has no \.pdf files"):
        list(source.links())


def test_files_missing(context, tmp_path):
    source = create(context, {"type": "files", "path": str(tmp_path / "missing")})

    with pytest.raises(FileNotFoundError, match="not found"):
        list(source.links())


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


def test_google_drive(context):
    source = create(context, {"type": "google_drive", "ids": [DOC, DOC]})

    assert [(link.location, link.url) for link in source.links()] == [
        ("google_drive:ids", f"https://drive.google.com/file/d/{DOC}/view")
    ]
    assert source.follow == {f"google_drive:{DOC}"}
    assert create(context, {"type": "google_drive", "ids": [DOC], "follow": False}).follow == set()


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


def test_github_exclude(context, monkeypatch):
    checked_out = []

    def checkout(_self, repo, _branch):
        checked_out.append(repo)
        return "directory", "main"

    monkeypatch.setattr(GitHubSource, "checkout", checkout)
    monkeypatch.setattr(GitHubSource, "files", lambda _self, _directory: iter([]))

    source = create(context, {"type": "github", "repos": ["o/a", "o/b"], "exclude": ["o/a"]})

    assert list(source.links()) == []
    assert checked_out == ["o/b"]


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


class FakeGitHub:
    def __init__(self, repos):
        self.repos = repos
        self.params = []

    def get(self, url, params=None, **kwargs):
        self.params.append(params)
        return SimpleNamespace(json=lambda: self.repos, raise_for_status=lambda: None)


REPOS = [
    {"full_name": "o/site", "private": True, "fork": False, "archived": False},
    {"full_name": "o/docs", "private": False, "fork": False, "archived": False},
]


def test_github_public_only(context):
    context.session = FakeGitHub(REPOS)
    source = create(context, {"type": "github", "orgs": ["o"]})

    assert source.repositories("o") == ["o/docs"]
    assert context.session.params[0]["type"] == "public"


def test_github_include_private(context, monkeypatch):
    monkeypatch.setenv("GITHUB_TOKEN", "secret")
    context.session = FakeGitHub(REPOS)
    runs = []
    monkeypatch.setattr(
        "walkrightin.sources.github.subprocess.run", lambda args, **kwargs: runs.append((args, kwargs["env"]))
    )
    source = create(context, {"type": "github", "orgs": ["o"], "include_private": True})

    assert source.repositories("o") == ["o/site", "o/docs"]
    assert context.session.params[0]["type"] == "all"

    source.git("fetch")
    args, env = runs[0]
    assert "secret" not in " ".join(args)
    assert env["GIT_CONFIG_KEY_0"] == "http.https://github.com/.extraheader"
    assert env["GIT_CONFIG_VALUE_0"] == "AUTHORIZATION: basic eC1hY2Nlc3MtdG9rZW46c2VjcmV0"


def test_github_include_private_requires_token(context, monkeypatch):
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)

    with pytest.raises(ValueError, match="include_private requires GITHUB_TOKEN"):
        create(context, {"type": "github", "orgs": ["o"], "include_private": True})
