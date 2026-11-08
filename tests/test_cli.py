import json

from click.testing import CliRunner

from walkrightin.check import Checker
from walkrightin.cli import main

from .conftest import DOC

DOC_URL = f"https://docs.google.com/document/d/{DOC}/edit"


def test_sources():
    result = CliRunner().invoke(main, ["sources"])

    assert result.exit_code == 0
    assert [line.split("\t")[0] for line in result.output.splitlines()] == [
        "csv",
        "drive",
        "files",
        "github",
        "wordpress",
    ]


def test_extract(tmp_path):
    path = tmp_path / "page.html"
    path.write_text(f'<a href="{DOC_URL}">Doc</a>')

    result = CliRunner().invoke(main, ["extract", str(path)])

    assert result.exit_code == 0
    header, row = result.output.splitlines()
    assert header.split("\t")[:4] == ["path", "url", "text", "part"]
    assert row.split("\t")[:4] == [str(path), DOC_URL, "Doc", "body"]


def test_check(monkeypatch):
    monkeypatch.setattr(Checker, "request", lambda _self, _url: {"status": "signin", "http": "302", "location": ""})

    result = CliRunner().invoke(main, ["check", DOC_URL])

    assert result.output == f"signin\t302\t{DOC_URL}\n"


def write_config(tmp_path):
    seeds = tmp_path / "seeds.csv"
    seeds.write_text(f"url,location\n{DOC_URL},page\n")
    config = tmp_path / "walkrightin.toml"
    config.write_text(
        f'cache_dir = "{tmp_path / "cache"}"\n[check]\ndelay = 0\n[[sources]]\ntype = "csv"\npath = "seeds.csv"\n'
    )
    return str(config)


def test_run_no_walk(tmp_path, monkeypatch):
    monkeypatch.setattr(Checker, "request", lambda _self, _url: {"status": "public", "http": "200", "location": ""})
    out = tmp_path / "out"

    result = CliRunner().invoke(main, ["run", "-c", write_config(tmp_path), "-o", str(out), "--no-walk"])

    assert result.exit_code == 0, result.output
    assert result.output == "1 links, 0 items\n"
    assert json.loads((out / "run.json").read_text())["links"] == {"csv": 1}


def test_run_rate_limited(tmp_path, monkeypatch):
    monkeypatch.setattr(
        Checker, "request", lambda _self, _url: {"status": "rate_limited", "http": "429", "location": ""}
    )

    result = CliRunner().invoke(main, ["run", "-c", write_config(tmp_path), "-o", str(tmp_path / "out"), "--no-walk"])

    assert result.exit_code == 1
    assert "1 checks were rate-limited" in result.output
