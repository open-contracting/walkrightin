"""
Platforms that links point to: each recognizes its URLs, identifies the item, checks it and reads it.

A platform is a subclass of :class:`Platform`, registered under the ``walkrightin.platforms`` entry-point group with
its ``name`` as the name. The name prefixes its items' keys (``name:id``) and names its ``[platforms.NAME]`` table.
"""

import abc
import functools
import re
from collections.abc import Iterator
from dataclasses import dataclass, field
from importlib.metadata import entry_points
from typing import Any, ClassVar

from walkrightin.model import IDENTITY
from walkrightin.urls import END

GROUP = "walkrightin.platforms"


def split_key(key):
    """Return (platform name, the platform's ID) for an item's key."""
    name, _, item_id = key.partition(":")
    return name, item_id


def join_key(name, item_id):
    """Return an item's key, from its platform's name and the platform's ID."""
    return f"{name}:{item_id}"


@dataclass
class Context:
    """The services that platforms share. ``extractor`` is set once the extractor exists, as it needs the platforms."""

    #: The ``[walk]`` table.
    walk: Any = None
    #: The most children of a container to list, or None for no limit.
    folder_limit: int | None = None
    #: The User-Agent of anonymous requests.
    user_agent: str = IDENTITY
    #: Downloaded files' links, by item key, with each file's version.
    downloads: dict = field(default_factory=dict)
    #: Finds links in text and files.
    extractor: Any = None


@dataclass
class Scan:
    """The result of reading an item through one channel."""

    #: How the item was read: export, download, api, comments, listing, or none if it wasn't.
    channel: str
    links: list = field(default_factory=list)
    error: str = ""
    note: str = ""
    #: Who read it, if not the item's ``read_as``.
    read_as: str | None = None
    #: False if the channel didn't read the item, like a type that isn't read.
    read: bool = True
    #: comments.csv rows, without ``item_id``.
    comments: list = field(default_factory=list)


@dataclass
class Options:
    """A platform without options."""


class Platform(abc.ABC):
    """
    A platform that links point to. Subclasses set the class variables, and take their options and a Context.

    Methods that take an ``item_id`` take the platform's ID, without the key's prefix. Item metadata is a dict of
    items.csv fields: ``link_role`` is the role of anyone with the link ("" if none, "?" if unknown), and ``read_as``
    is who can read the item, or "" if only an anonymous visitor might.
    """

    name: ClassVar[str]
    #: The start of the platform's URLs, as a regular expression. A URL runs on to the characters that end it.
    pattern: ClassVar[str]
    #: A host, as a regular expression, that can end a line where a PDF splits a URL.
    hosts: ClassVar[str] = ""
    #: The dataclass of the ``[platforms.NAME]`` table.
    options: ClassVar[type] = Options
    #: The options that are paths, resolved relative to the config file.
    paths: ClassVar[tuple] = ()
    #: Environment variables that override options, as {option: variable}.
    environ: ClassVar[dict] = {}

    def __init__(self, options, context=None):
        self.config = options
        self.context = context or Context()
        self.regex = re.compile(self.pattern + END)

    @property
    def credentialed(self):
        """Return whether the platform can read items as the organization, rather than only anonymously."""
        return False

    # Recognize

    def identifies(self, url):
        """Return whether a URL that matches ``pattern`` identifies an item, unlike a template or a home page."""
        return True

    @abc.abstractmethod
    def key(self, url) -> tuple[str, str]:
        """Return (the item's ID or "", the resource key that the URL carries or "")."""

    def url(self, item_id):
        """Return a URL for the item, like for an ID that a source configures or a text names."""
        raise NotImplementedError

    def bare_ids(self, text) -> set[str]:
        """Return the strings in the text that look like the platform's IDs outside a URL."""
        return set()

    def continues(self, url) -> list[str]:
        """Return the URLs that a sign-in page continues to, to follow a short link through it."""
        return []

    # Check

    def check_url(self, url):
        """Return the URL to request to check a URL anonymously."""
        return url

    def redirect_status(self, location):
        """Return the status of a check that redirects to the location, like ``signin``, or None to follow it."""
        return

    @abc.abstractmethod
    def item_check_url(self, item_id, meta) -> str:
        """Return the URL to request to check an item anonymously."""

    # Inspect

    def describe(self, item_id, resource_key, url) -> dict:
        """Return the item's metadata: at least ``link_role``, ``read_as``, ``mime_type``, ``error``."""
        return {"link_role": "?", "read_as": "", "mime_type": "", "error": "", "resource_key": resource_key}

    def after_check(self, meta, result):  # noqa: B027 # optional
        """Update the item's metadata from its anonymous check."""

    def owns(self, meta):
        """Return whether the organization owns the item."""
        return False

    # Read

    @abc.abstractmethod
    def location(self, item_id, meta) -> str:
        """Return the URL to name the item by, as the location of the links in it."""

    @abc.abstractmethod
    def read(self, item_id, meta) -> list[Scan]:
        """Return the item's Scans, one per channel, with Links without a location."""

    def listing(self, children):
        """
        Return a Scan of a container's children, or None if containers aren't listed.

        ``children(limit)`` returns (Links, error), with at most ``limit`` + 1 Links.
        """
        limit = self.context.folder_limit
        if limit == 0:
            return None
        limit = 10**9 if limit is None else limit
        found, error = children(limit)
        if error:
            return Scan("listing", error=error)
        if len(found) > limit:
            return Scan("listing", note=f"not walked: more than {limit} items")
        return Scan("listing", found)


class Platforms:
    """The platforms of a run, by name."""

    def __init__(self, platforms):
        self.by_name = dict(platforms)
        patterns = "|".join(f"(?:{p.pattern})" for p in self)
        #: Matches any platform's URL.
        self.regex = re.compile(f"(?:{patterns}){END}" if patterns else "(?!)")
        hosts = "|".join(p.hosts for p in self if p.hosts)
        #: Matches any platform's host, where a PDF can split a URL.
        self.hosts = re.compile(hosts) if hosts else None

    def __iter__(self) -> Iterator[Platform]:
        return iter(self.by_name.values())

    def __getitem__(self, name):
        return self.by_name[name]

    def match(self, url):
        """Return the platform whose URL the URL is, or None."""
        return next((p for p in self if p.regex.match(url)), None)

    def finditer(self, text):
        """Yield the matches of platforms' URLs in the text."""
        return self.regex.finditer(text)

    def identifies(self, url):
        """Return whether the URL is a platform's and identifies an item."""
        return bool((platform := self.match(url)) and platform.identifies(url))

    def key(self, url):
        """Return (the item's key or "", its resource key or "") for a URL."""
        if not (platform := self.match(url)):
            return "", ""
        item_id, resource_key = platform.key(url)
        return (join_key(platform.name, item_id) if item_id else ""), resource_key

    def item(self, key):
        """Return (platform, the platform's ID) for an item's key."""
        name, item_id = split_key(key)
        return self.by_name[name], item_id

    def bare_ids(self, text):
        """Yield (key, URL) for the strings in the text that look like a platform's IDs outside a URL."""
        for platform in self:
            for item_id in platform.bare_ids(text):
                yield join_key(platform.name, item_id), platform.url(item_id)

    def target(self, url):
        """Return the platform URL that a URL is, or that a sign-in page continues to, or None."""
        if self.match(url):
            return url
        return next((u for p in self for u in p.continues(url) if self.match(u)), None)

    def check_url(self, url):
        return platform.check_url(url) if (platform := self.match(url)) else url

    def redirect_status(self, url, location):
        """Return the status of a check of the URL that redirects to the location, or None to follow it."""
        platforms = [platform] if (platform := self.match(url)) else list(self)
        return next((s for p in platforms if (s := p.redirect_status(location))), None)


def registry():
    """Return {name: Platform subclass} for the installed platforms."""
    return {ep.name: ep.load() for ep in entry_points(group=GROUP)}


def load(options=None, context=None):
    """Return the installed Platforms, with ``options`` ({name: options}) or each one's defaults."""
    options = options or {}
    return Platforms(
        (name, cls(options.get(name) or cls.options(), context)) for name, cls in sorted(registry().items())
    )


@functools.cache
def default():
    """Return the installed Platforms with their default options, to recognize and check URLs."""
    return load()
