"""Read a walkrightin.toml file."""

import dataclasses
import os
import tomllib
from dataclasses import dataclass, field
from pathlib import Path

from walkrightin.model import IDENTITY
from walkrightin.platforms import registry

CHANNELS = ("export", "api", "comments", "metadata")


@dataclass
class Walk:
    max_depth: int = 8
    #: A file of item keys (like ``google_drive:ID``), one per line, to follow whatever their check.
    also_follow: str | None = None
    max_download_mb: int = 200
    max_text_kb: int = 1000
    #: 0 disables walking folders, and "all" walks every folder whatever its size.
    max_folder_children: int | str = 1000
    channels: list = field(default_factory=lambda: list(CHANNELS))
    #: The most files owned outside the organization to read in a row, from a source or an own file. None is no limit.
    max_outside_hops: int | None = None
    #: Whether to read CSV files. Off by default: their URLs are usually data, not links.
    download_csv: bool = False
    workers: int = 4


@dataclass
class Check:
    #: "all" checks every item anonymously; "unreadable" checks only those whose link role can't be read.
    items: str = "all"
    concurrency: int = 2
    delay: float = 1.0
    ttl_days: int = 7
    #: The User-Agent of anonymous requests to platforms, if not walkrightin's.
    user_agent: str = IDENTITY


@dataclass
class ShortLinks:
    #: Replaces the built-in list of shortener domains.
    domains: list | None = None
    extra_domains: list = field(default_factory=list)
    exclude_domains: list = field(default_factory=list)


@dataclass
class Config:
    walk: Walk = field(default_factory=Walk)
    check: Check = field(default_factory=Check)
    short_links: ShortLinks = field(default_factory=ShortLinks)
    #: Each platform's options, by name: an instance of the platform's ``options`` dataclass.
    platforms: dict = field(default_factory=dict)
    sources: list = field(default_factory=list)
    cache_dir: str = "~/.cache/walkrightin"
    #: Extra redirect wrappers, as [host suffix, path, [query parameters]].
    wrappers: list = field(default_factory=list)

    @property
    def folder_limit(self):
        """Return the most children of a folder to walk, or None for no limit."""
        value = self.walk.max_folder_children
        return None if value == "all" else int(value)


def table(cls, data, name):
    allowed = {f.name for f in dataclasses.fields(cls)}
    if unknown := set(data) - allowed:
        msg = f"unknown key(s) in [{name}]: {', '.join(sorted(unknown))}"
        raise ValueError(msg)
    return cls(**data)


# The options, by table, that are paths, resolved relative to the config file.
PATHS = {"walk": ("also_follow",)}
SOURCE_PATHS = ("path",)


def load(path=None, data=None):
    """Return the Config in the TOML file, or in ``data``. Relative paths are relative to the file."""
    base = Path.cwd()
    if path is not None:
        path = Path(path)
        data = tomllib.loads(path.read_text())
        base = path.resolve().parent
    data = dict(data or {})

    def resolve(value):
        # Paths stay strings, so that the config serializes to JSON in run.json.
        value = Path(value).expanduser()
        return str(value if value.is_absolute() else base / value)

    for name, keys in PATHS.items():
        for key in keys:
            if data.get(name, {}).get(key):
                data[name] = {**data[name], key: resolve(data[name][key])}
    classes = registry()
    tables = data.pop("platforms", {})
    if unknown := set(tables) - set(classes):
        msg = f"unknown platform(s): {', '.join(sorted(unknown))} (installed: {', '.join(sorted(classes))})"
        raise ValueError(msg)
    platforms = {}
    for name, cls in sorted(classes.items()):
        options = dict(tables.get(name, {}))
        for key in cls.paths:
            if options.get(key):
                options[key] = resolve(options[key])
        for key, variable in cls.environ.items():
            if value := os.getenv(variable):
                options[key] = value
        platforms[name] = table(cls.options, options, f"platforms.{name}")
    sources = []
    for source in data.pop("sources", []):
        if "type" not in source:
            msg = "each [[sources]] needs a type"
            raise ValueError(msg)
        sources.append(
            {k: resolve(v) if k in SOURCE_PATHS and source["type"] != "github" else v for k, v in source.items()}
        )
    names = [s.get("name") or s["type"] for s in sources]
    if len(names) != len(set(names)):
        msg = "give each [[sources]] of the same type a distinct name"
        raise ValueError(msg)
    config = Config(
        walk=table(Walk, data.pop("walk", {}), "walk"),
        check=table(Check, data.pop("check", {}), "check"),
        short_links=table(ShortLinks, data.pop("short_links", {}), "short_links"),
        platforms=platforms,
        sources=sources,
        cache_dir=str(Path(data.pop("cache_dir", Config.cache_dir)).expanduser()),
        wrappers=data.pop("wrappers", []),
    )
    if data:
        msg = f"unknown key(s): {', '.join(sorted(data))}"
        raise ValueError(msg)
    if config.check.items not in ("all", "unreadable"):
        msg = '[check] items must be "all" or "unreadable"'
        raise ValueError(msg)
    if unknown := set(config.walk.channels) - set(CHANNELS):
        msg = f"unknown channel(s): {', '.join(sorted(unknown))}"
        raise ValueError(msg)
    hops = config.walk.max_outside_hops
    if hops is not None and not (isinstance(hops, int) and hops >= 0):
        msg = "[walk] max_outside_hops must be a non-negative integer"
        raise ValueError(msg)
    if config.walk.max_folder_children != "all" and not (
        isinstance(config.walk.max_folder_children, int) and config.walk.max_folder_children >= 0
    ):
        msg = '[walk] max_folder_children must be a non-negative integer or "all"'
        raise ValueError(msg)
    return config
