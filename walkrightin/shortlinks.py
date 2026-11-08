"""Recognize short links (bit.ly, forms.gle, etc.) and follow them to the platforms' items they lead to."""

import re
import threading
from urllib.parse import urljoin, urlsplit

import requests
from antispam_link_shorteners import link_shorteners_list

from walkrightin import platforms as platforms_module
from walkrightin.model import IDENTITY
from walkrightin.output import read_json, write_json
from walkrightin.urls import END, normalize_short

# A host and path, with or without a scheme. The host must start the match, so that "t.co/" isn't matched in
# "microsoft.co/".
CANDIDATE = re.compile(rf"(?<![\w.@-])(?:https?://)?(?:www\.)?((?:[a-z0-9-]+\.)+[a-z]{{2,}})/{END}", re.IGNORECASE)
MAX_REDIRECTS = 10
# Shorteners in the built-in list that lead only to their own site, never to a platform.
SINGLE_SITE = {
    "a.co",
    "amzn.to",
    "apple.co",
    "fb.me",
    "flic.kr",
    "m.me",
    "redd.it",
    "spoti.fi",
    "t.me",
    "wa.me",
    "wp.me",
    "youtu.be",
}


class ShortLinks:
    """
    Short-link domains, and the platform URLs that short links lead to, cached.

    The default domains are antispam-link-shorteners' list, without SINGLE_SITE. ``domains`` replaces them.
    ``platforms`` defaults to the installed platforms.
    """

    def __init__(
        self,
        domains=None,
        extra_domains=(),
        exclude_domains=(),
        session=None,
        timeout=20,
        user_agent=IDENTITY,
        platforms=None,
    ):
        self.platforms = platforms or platforms_module.default()
        self.user_agent = user_agent
        base = set(link_shorteners_list()) - SINGLE_SITE if domains is None else {d.lower() for d in domains}
        self.domains = (base | {d.lower() for d in extra_domains}) - {d.lower() for d in exclude_domains}
        self.session = session or requests.Session()
        self.timeout = timeout
        self._cache = {}
        self._lock = threading.Lock()

    def is_short(self, url):
        try:
            host = urlsplit(normalize_short(url)).netloc.lower()
        except ValueError:  # an href like "[r]"
            return False
        return host.removeprefix("www.") in self.domains

    def finditer(self, text):
        """Yield the matches of short links in the text."""
        for m in CANDIDATE.finditer(text):
            if m.group(1).lower().removeprefix("www.") in self.domains:
                yield m

    def follow(self, url):
        """Return (the first platform URL in the short link's redirects or None, the final status, the final URL)."""
        url = normalize_short(url)
        with self._lock:
            if url in self._cache:
                return self._cache[url]
        result = self._follow(url)
        with self._lock:
            self._cache[url] = result
        return result

    def load(self, path):
        """Read followed short links from a JSON cache."""
        self._cache.update({url: tuple(result) for url, result in read_json(path, {}).items()})

    def save(self, path):
        """Write followed short links to a JSON cache, except those whose request failed."""
        with self._lock:
            cache = {url: result for url, result in self._cache.items() if result[1] != "000"}
        write_json(path, cache)

    def resolve(self, url):
        """Return the platform URL that the short link leads to, or None."""
        return self.follow(url)[0]

    def _follow(self, url):
        current, status = url, "000"
        for _ in range(MAX_REDIRECTS + 1):
            if found := self.platforms.target(current):
                return found, status, current
            try:
                response = self.session.get(
                    current,
                    headers={"User-Agent": self.user_agent},
                    allow_redirects=False,
                    timeout=self.timeout,
                    stream=True,
                )
                response.close()
            except requests.RequestException:
                return None, "000", current
            status = str(response.status_code)
            location = response.headers.get("Location")
            if not response.is_redirect or not location:
                return self.platforms.target(current), status, current
            current = urljoin(current, location)
        return self.platforms.target(current), status, current
