from types import SimpleNamespace

import pytest

from walkrightin.check import Checker
from walkrightin.shortlinks import ShortLinks

from .conftest import DOC

DOC_URL = f"https://docs.google.com/document/d/{DOC}/edit"


def response(code, location=""):
    return SimpleNamespace(
        status_code=code,
        headers={"Location": location} if location else {},
        is_redirect=bool(location) and 300 <= code < 400,
        close=lambda: None,
    )


class FakeSession:
    def __init__(self, responses):
        self.responses = responses
        self.requested = []

    def get(self, url, **kwargs):
        self.requested.append(url)
        return self.responses[url]


@pytest.mark.parametrize(
    ("responses", "status", "http"),
    [
        ({DOC_URL: response(200)}, "public", "200"),
        ({DOC_URL: response(404)}, "not_found", "404"),
        ({DOC_URL: response(410)}, "gone", "410"),
        ({DOC_URL: response(401)}, "signin", "401"),
        ({DOC_URL: response(429)}, "rate_limited", "429"),
        ({DOC_URL: response(500)}, "error", "500"),
        ({DOC_URL: response(302, "https://accounts.google.com/ServiceLogin?continue=x")}, "signin", "302"),
        ({DOC_URL: response(302, "https://www.google.com/sorry/index?continue=x")}, "rate_limited", "302"),
        ({DOC_URL: response(302, "https://docs.google.com/forms/d/x/closedform")}, "closed_form", "302"),
        (
            {DOC_URL: response(307, "https://docs.google.com/x"), "https://docs.google.com/x": response(200)},
            "redirect",
            "307",
        ),
        # A redirect to another Google page that asks to sign in.
        (
            {
                DOC_URL: response(302, "https://docs.google.com/x"),
                "https://docs.google.com/x": response(302, "https://accounts.google.com/v3/signin"),
            },
            "signin",
            "302",
        ),
    ],
)
def test_check(responses, status, http):
    checker = Checker(delay=0, session=FakeSession(responses))

    result = checker.check(DOC_URL)

    assert (result["status"], result["http"]) == (status, http)


def test_check_copy_and_cache(tmp_path):
    session = FakeSession({DOC_URL: response(200)})
    cache = tmp_path / "checks.json"
    checker = Checker(str(cache), delay=0, session=session)

    checker.check_all([DOC_URL.replace("/edit", "/copy"), DOC_URL])

    assert session.requested == [DOC_URL]
    assert Checker(str(cache), delay=0, session=FakeSession({})).check(DOC_URL)["status"] == "public"


def test_rate_limited_not_cached(tmp_path):
    cache = tmp_path / "checks.json"
    Checker(str(cache), delay=0, session=FakeSession({DOC_URL: response(429)})).check_all([DOC_URL])

    assert Checker(str(cache), delay=0, session=FakeSession({DOC_URL: response(200)})).check(DOC_URL)["status"] == (
        "public"
    )


def test_shortlinks_follow(tmp_path):
    session = FakeSession(
        {
            "https://bit.ly/x": response(301, "https://forms.gle/y"),
            "https://forms.gle/y": response(302, f"https://accounts.google.com/signin?continue={DOC_URL}"),
        }
    )
    shortlinks = ShortLinks(session=session)

    assert shortlinks.follow("bit.ly/x") == (DOC_URL, "302", f"https://accounts.google.com/signin?continue={DOC_URL}")

    path = str(tmp_path / "short.json")
    shortlinks.save(path)
    cached = ShortLinks(session=FakeSession({}))
    cached.load(path)
    assert cached.resolve("https://bit.ly/x") == DOC_URL


def test_shortlinks_domains():
    shortlinks = ShortLinks(domains=["bit.ly"], extra_domains=["ocp.click"], exclude_domains=["bit.ly"])

    assert shortlinks.is_short("ocp.click/x")
    assert not shortlinks.is_short("bit.ly/x")
    assert not shortlinks.is_short("[r]")
    assert not ShortLinks().is_short("https://youtu.be/x")
    assert [m.group() for m in shortlinks.finditer("see ocp.click/a and xocp.click/b")] == ["ocp.click/a"]
