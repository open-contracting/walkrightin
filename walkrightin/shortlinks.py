"""Recognize short links (bit.ly, forms.gle, etc.) and follow them to the Drive files they lead to."""

import json
import os
import re
import threading
from urllib.parse import parse_qs, unquote, urljoin, urlsplit

import requests
from antispam_link_shorteners import link_shorteners_list

from walkrightin.urls import DRIVE, END, normalize_short

# A host and path, with or without a scheme. The host must start the match, so that "t.co/" isn't matched in
# "microsoft.co/".
CANDIDATE = re.compile(rf"(?<![\w.@-])(?:https?://)?(?:www\.)?((?:[a-z0-9-]+\.)+[a-z]{{2,}})/{END}", re.IGNORECASE)
MAX_REDIRECTS = 10
USER_AGENT = "Mozilla/5.0"
# Shorteners in the built-in list that lead only to their own site, never to Drive.
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
    Short-link domains, and the Drive URLs that short links lead to, cached.

    The default domains are antispam-link-shorteners' list, without SINGLE_SITE. ``domains`` replaces them.
    """

    def __init__(self, domains=None, extra_domains=(), exclude_domains=(), session=None, timeout=20):
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
        """Return (the first Drive URL in the short link's redirects or None, the final status, the final URL)."""
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
        if os.path.exists(path):
            with open(path) as f:
                self._cache.update({url: tuple(result) for url, result in json.load(f).items()})

    def save(self, path):
        """Write followed short links to a JSON cache, except those whose request failed."""
        os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
        with self._lock:
            cache = {url: result for url, result in self._cache.items() if result[1] != "000"}
        with open(path + ".tmp", "w") as f:
            json.dump(cache, f, indent=1, sort_keys=True)
        os.replace(path + ".tmp", path)

    def resolve(self, url):
        """Return the Drive URL that the short link leads to, or None."""
        return self.follow(url)[0]

    def _follow(self, url):
        current, status = url, "000"
        for _ in range(MAX_REDIRECTS + 1):
            if found := drive_target(current):
                return found, status, current
            try:
                response = self.session.get(
                    current,
                    headers={"User-Agent": USER_AGENT},
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
                return drive_target(current), status, current
            current = urljoin(current, location)
        return drive_target(current), status, current


def drive_target(url):
    """Return the Drive URL that a URL is, or that a sign-in page continues to, or None."""
    if DRIVE.match(url):
        return url
    query = parse_qs(urlsplit(url).query)
    for value in query.get("continue", []) + query.get("followup", []):
        if DRIVE.match(unquote(value)):
            return unquote(value)
    return None
