"""
Sources of links: each yields where a link was found, its URL and its text.

A source is a subclass of :class:`Source`, registered under the ``walkrightin.sources`` entry-point group with its
``type`` as the name. For example, in a plugin's ``pyproject.toml``:

.. code-block:: toml

   [project.entry-points."walkrightin.sources"]
   sharepoint = "walkrightin_sharepoint:SharePointSource"
"""

import abc
import inspect
from collections.abc import Iterator
from dataclasses import dataclass
from importlib.metadata import entry_points
from typing import Any, ClassVar

from walkrightin.extract import Extractor
from walkrightin.model import Link

GROUP = "walkrightin.sources"


@dataclass
class Context:
    """The services that sources share."""

    #: Finds links in text and files. A source with ``bare_ids`` gets its own.
    extractor: Extractor
    #: The directory for caches, like clones.
    cache_dir: str
    #: The largest text file to read, in bytes.
    max_text_bytes: int
    #: An HTTP session.
    session: Any
    #: The platforms, for a source that lists a platform's items. By default, the extractor's.
    platforms: Any = None

    def __post_init__(self):
        if self.platforms is None:
            self.platforms = self.extractor.platforms


class Source(abc.ABC):
    """A source of links. Subclasses set ``type`` and take their options as keyword arguments."""

    type: ClassVar[str]

    def __init__(self, context: Context, *, name: str = "", bare_ids: bool = False):
        self.context = context
        self.name = name or self.type
        self.extractor = context.extractor
        if bare_ids:
            self.extractor = Extractor(
                context.extractor.shortlinks, bare_ids=True, wrappers=context.extractor.wrappers
            )
        #: Item keys to follow whatever their check, like the items that a platform's source names.
        self.follow = set()
        #: Files not read because of a size limit.
        self.skipped_for_size = 0

    @abc.abstractmethod
    def links(self) -> Iterator[Link]:
        """Yield each link, with its location."""


def registry():
    """Return {type: Source subclass} for the installed sources."""
    return {ep.name: ep.load() for ep in entry_points(group=GROUP)}


def create(context, options):
    """Return the source that ``options`` (a ``[[sources]]`` table) configures."""
    options = dict(options)
    kind = options.pop("type")
    classes = registry()
    if kind not in classes:
        msg = f"unknown source type {kind!r} (installed: {', '.join(sorted(classes))})"
        raise ValueError(msg)
    cls = classes[kind]
    allowed = set(inspect.signature(cls.__init__).parameters) | {"name", "bare_ids"}
    if unknown := set(options) - allowed:
        msg = f"unknown option(s) for source {kind!r}: {', '.join(sorted(unknown))}"
        raise ValueError(msg)
    return cls(context, **options)
