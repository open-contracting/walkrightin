import dataclasses
import logging
import os
from collections import Counter
from concurrent.futures import ThreadPoolExecutor

from walkrightin.sources import Source

logger = logging.getLogger(__name__)

EXTENSIONS = (".pdf", ".doc", ".docx", ".ppt", ".pptx", ".xls", ".xlsx", ".csv")


class FilesSource(Source):
    """
    The files in a directory, like a website's uploads: PDF, Word, PowerPoint, Excel and CSV files by default.

    Each link's location is ``location_prefix`` plus the file's path relative to ``path``.
    """

    type = "files"

    def __init__(self, context, *, path, location_prefix="", extensions=EXTENSIONS, workers=6, **kwargs):
        super().__init__(context, **kwargs)
        self.path = os.path.expanduser(path)
        self.location_prefix = location_prefix
        self.extensions = tuple(e.lower() for e in extensions)
        self.workers = workers
        #: Files not read, by type.
        self.skipped = Counter()
        #: (location, error) for files whose reading failed.
        self.errors = []

    def links(self):
        if not os.path.isdir(self.path):
            msg = f"{self.path} not found"
            raise FileNotFoundError(msg)
        paths = []
        for folder, _, names in os.walk(self.path):
            for name in names:
                ext = os.path.splitext(name)[1].lower()
                if ext in self.extensions:
                    paths.append(os.path.join(folder, name))
                else:
                    self.skipped[ext or "no type"] += 1
        paths.sort()
        with ThreadPoolExecutor(self.workers) as pool:
            results = list(pool.map(self.extractor.file, paths))
        for path, (found, error) in zip(paths, results, strict=True):
            location = self.location_prefix + os.path.relpath(path, self.path)
            if error:
                self.errors.append((location, error))
                logger.warning("%s: %s", location, error)
            for link in found:
                yield dataclasses.replace(link, location=location)
        if self.skipped:
            logger.info("skipped, by type: %s", dict(self.skipped.most_common()))
