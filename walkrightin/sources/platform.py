from typing import ClassVar

from walkrightin.model import Link
from walkrightin.platforms import join_key
from walkrightin.sources import Source


class PlatformSource(Source):
    """
    A platform's items, by ID, to read and walk from, whether or not they are public. Subclasses set ``platform``.

    Each item is a link at depth 0, located at "TYPE:ids". With ``follow`` (the default), the walk reads each item even
    if it doesn't open anonymously.
    """

    platform: ClassVar[str]

    def __init__(self, context, *, ids, follow=True, **kwargs):
        super().__init__(context, **kwargs)
        self.ids = list(dict.fromkeys(ids))
        if follow:
            self.follow = {join_key(self.platform, i) for i in self.ids}

    def links(self):
        platform = self.context.platforms[self.platform]
        for item_id in self.ids:
            yield Link(f"{self.type}:ids", platform.url(item_id))
