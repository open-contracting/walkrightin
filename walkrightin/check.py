"""Check how an anonymous visitor sees a URL, with a cache and rate-limit handling."""

import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from http import HTTPStatus
from urllib.parse import urljoin

import requests

from walkrightin.model import CLOSED_FORM, ERROR, GONE, IDENTITY, NOT_FOUND, PUBLIC, RATE_LIMITED, REDIRECT, SIGNIN
from walkrightin.output import read_json, write_json
from walkrightin.urls import check_url

MAX_REDIRECTS = 10


def now():
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def signin(url):
    return "accounts.google.com" in url or "/ServiceLogin" in url


def throttled(url):
    return "google.com/sorry" in url


class Checker:
    """Requests URLs anonymously, at most ``concurrency`` at once, waiting ``delay`` seconds before each."""

    def __init__(
        self, cache_path=None, ttl_days=7, concurrency=2, delay=1.0, session=None, timeout=20, user_agent=IDENTITY
    ):
        self.user_agent = user_agent
        self.cache_path = cache_path
        self.ttl = timedelta(days=ttl_days)
        self.concurrency = concurrency
        self.delay = delay
        self.session = session or requests.Session()
        self.timeout = timeout
        self._lock = threading.Lock()
        self.cache = read_json(cache_path, {}) if cache_path else {}

    def check(self, url):
        """Return {"status", "http", "location", "checked_at"} for the URL, from the cache if fresh."""
        key = check_url(url)
        with self._lock:
            cached = self.cache.get(key)
        if cached and datetime.now(UTC) - datetime.fromisoformat(cached["checked_at"]) < self.ttl:
            return cached
        if self.delay:
            time.sleep(self.delay)
        result = {**self.request(key), "checked_at": now()}
        if result["status"] != RATE_LIMITED:
            with self._lock:
                self.cache[key] = result
        return result

    def check_all(self, urls):
        """Return {URL: result} for the distinct URLs, requesting each URL to check once."""
        urls = list(dict.fromkeys(urls))
        keys = list(dict.fromkeys(check_url(url) for url in urls))
        with ThreadPoolExecutor(self.concurrency) as pool:
            results = dict(zip(keys, pool.map(self.check, keys), strict=True))
        self.save()
        return {url: results[check_url(url)] for url in urls}

    def save(self):
        if not self.cache_path:
            return
        with self._lock:
            cache = dict(self.cache)
        write_json(self.cache_path, cache)

    def get(self, url):
        response = self.session.get(
            url, headers={"User-Agent": self.user_agent}, allow_redirects=False, timeout=self.timeout, stream=True
        )
        response.close()
        return response

    def request(self, url):
        """Request the URL and follow its redirects. Return {"status", "http", "location"}."""
        try:
            response = self.get(url)
        except requests.RequestException as e:
            return {"status": ERROR, "http": "", "location": str(e)[:200]}
        http = str(response.status_code)
        first = response.headers.get("Location", "")
        location = first
        redirected = False
        current = url
        for _ in range(MAX_REDIRECTS):
            if not response.is_redirect or not location:
                break
            redirected = True
            if signin(location):
                return {"status": SIGNIN, "http": http, "location": first}
            if throttled(location):
                return {"status": RATE_LIMITED, "http": http, "location": first}
            if "/closedform" in location:
                return {"status": CLOSED_FORM, "http": http, "location": first}
            current = urljoin(current, location)
            try:
                response = self.get(current)
            except requests.RequestException as e:
                return {"status": ERROR, "http": http, "location": str(e)[:200]}
            location = response.headers.get("Location", "")
        return {"status": classify(response.status_code, redirected), "http": http, "location": first}


def classify(code, redirected):
    """Return the status for the final response's code."""
    if code == HTTPStatus.TOO_MANY_REQUESTS:
        return RATE_LIMITED
    if code == HTTPStatus.UNAUTHORIZED:
        return SIGNIN
    if code == HTTPStatus.NOT_FOUND:
        return NOT_FOUND
    if code == HTTPStatus.GONE:
        return GONE
    if HTTPStatus.OK <= code < HTTPStatus.MULTIPLE_CHOICES:
        return REDIRECT if redirected else PUBLIC
    return ERROR
