from walkrightin.model import Link
from walkrightin.sources import Source
from walkrightin.urls import file_url


class DriveSource(Source):
    """
    Drive files, by ID, to read and walk from, whether or not they are public.

    Each file is a link at depth 0, located at "drive:ids". With ``follow`` (the default), the walk reads each file
    even if it doesn't open anonymously, like a private file to scan for links. It can read a private file only with
    credentials.
    """

    type = "drive"

    def __init__(self, context, *, ids, follow=True, **kwargs):
        super().__init__(context, **kwargs)
        self.ids = list(dict.fromkeys(ids))
        if follow:
            self.follow = set(self.ids)

    def links(self):
        for file_id in self.ids:
            yield Link("drive:ids", file_url(file_id))
