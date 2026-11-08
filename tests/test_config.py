import pytest

from walkrightin import config


def write(tmp_path, text):
    path = tmp_path / "walkrightin.toml"
    path.write_text(text)
    return str(path)


def test_defaults():
    c = config.load(data={})

    assert c.walk.max_depth == 8
    assert c.folder_limit == 1000
    assert c.check.items == "all"
    assert c.walk.download_csv is False


def test_paths_relative_to_file(tmp_path):
    c = config.load(
        write(
            tmp_path,
            """
[walk]
also_follow = "ids.txt"
max_folder_children = "all"

[[sources]]
type = "files"
path = "uploads"

[[sources]]
type = "github"
orgs = ["o"]
""",
        )
    )

    assert c.walk.also_follow == str(tmp_path / "ids.txt")
    assert c.sources[0]["path"] == str(tmp_path / "uploads")
    assert c.folder_limit is None


def test_platform_paths_relative_to_file(tmp_path):
    c = config.load(write(tmp_path, '[platforms.google_drive]\ndrive_readers = "drives.csv"\n'))

    assert c.platforms["google_drive"].drive_readers == str(tmp_path / "drives.csv")
    assert c.platforms["google_drive"].aliases is None


def test_credentials_env(monkeypatch):
    monkeypatch.setenv("WALKRIGHTIN_GOOGLE_DRIVE_CREDENTIALS", "/key.json")
    data = {"platforms": {"google_drive": {"credentials": "other.json"}}}

    assert config.load(data=data).platforms["google_drive"].credentials == "/key.json"


@pytest.mark.parametrize(
    ("data", "message"),
    [
        ({"walk": {"depth": 1}}, r"unknown key\(s\) in \[walk\]: depth"),
        ({"colour": 1}, r"unknown key\(s\): colour"),
        ({"platforms": {"fax": {}}}, r"unknown platform\(s\): fax"),
        ({"platforms": {"google_drive": {"colour": 1}}}, r"unknown key\(s\) in \[platforms.google_drive\]: colour"),
        ({"check": {"items": "some"}}, "items must be"),
        ({"walk": {"channels": ["fax"]}}, r"unknown channel\(s\): fax"),
        ({"walk": {"max_folder_children": -1}}, "max_folder_children must be"),
        ({"walk": {"max_folder_children": "many"}}, "max_folder_children must be"),
        ({"sources": [{"path": "x"}]}, "needs a type"),
        ({"sources": [{"type": "csv", "path": "a"}, {"type": "csv", "path": "b"}]}, "distinct name"),
    ],
)
def test_invalid(data, message):
    with pytest.raises(ValueError, match=message):
        config.load(data=data)
