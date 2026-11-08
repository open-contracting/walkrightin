"""Clean URLs found in text, and unwrap redirect wrappers."""

import html
import re
from urllib.parse import parse_qs, urlsplit

# The characters that end a URL in running text, markup or code.
END = r"""[^\s"'<>\\(){}|\[\]]+"""
# Characters that end a URL, as entities in markup.
ENCODED_END = re.compile(r"&(?:quot|apos|lt|gt|#34|#39|#x22|#x27);", re.IGNORECASE)
UNICODE_ESCAPE = re.compile(r"\\+u([0-9a-fA-F]{4})")
# Redirect wrappers, as (host suffix, path, query parameters holding the destination).
WRAPPERS = [
    ("google.com", "/url", ("q", "url")),
    ("safelinks.protection.outlook.com", "/", ("url",)),
    ("l.facebook.com", "/l.php", ("u",)),
]


def clean(text):
    r"""Decode the JSON escapes in markup and code (\u002D, \/), and drop line-break hints."""
    text = UNICODE_ESCAPE.sub(lambda m: chr(int(m.group(1), 16)), text)
    text = re.sub(r"\\+/", "/", text)
    # Link text like "https://bit.ly/ocds-api-<wbr />discussion" would otherwise end at the tag.
    return re.sub(r"<wbr\s*/?>|&shy;|\u00ad", "", text)


def strip(url):
    """Cut the URL at an encoded quote or bracket; decode entities; drop zero-width characters and end punctuation."""
    url = ENCODED_END.split(url, maxsplit=1)[0]
    return re.sub("[\u200b-\u200d\ufeff]", "", html.unescape(url)).rstrip(".,;:!")


def unwrap(url, wrappers=WRAPPERS):
    """Return the destination of a redirect wrapper, like google.com/url?q=..., or the URL."""
    try:
        parts = urlsplit(url)
    except ValueError:  # an href like "[r]"
        return url
    host = parts.netloc.lower().split(":")[0]
    for suffix, path, params in wrappers:
        if (host == suffix or host.endswith("." + suffix)) and parts.path == path:
            query = parse_qs(parts.query)
            for param in params:
                if query.get(param):
                    return query[param][0]
    return url


def is_wrapper(url, wrappers=WRAPPERS):
    return unwrap(url, wrappers) != url


def normalize_short(url):
    return re.sub(r"^(?:https?://)?(?:www\.)?", "https://", url).rstrip("/")
