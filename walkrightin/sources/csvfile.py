import csv
import os

from walkrightin.model import Link
from walkrightin.sources import Source


class CSVSource(Source):
    """
    Links listed in a CSV file, like an earlier run's or another tool's output.

    ``url``, ``location`` and ``text`` name the columns. With ``separator``, a cell can hold several values, like
    pages joined by " | ". A row yields a link for each location; its text applies to each.
    """

    type = "csv"

    def __init__(self, context, *, path, url="url", location="location", text=None, separator=None, **kwargs):
        super().__init__(context, **kwargs)
        self.path = os.path.expanduser(path)
        self.url = url
        self.location = location
        self.text = text
        self.separator = separator

    def split(self, value):
        value = value or ""
        if self.separator:
            return [v.strip() for v in value.split(self.separator) if v.strip()]
        return [value] if value else []

    def links(self):
        with open(self.path, newline="") as f:
            for row in csv.DictReader(f):
                text = row.get(self.text, "") if self.text else ""
                for url in self.split(row.get(self.url)):
                    for location in self.split(row.get(self.location)) or [self.path]:
                        yield Link(location, url, text)
