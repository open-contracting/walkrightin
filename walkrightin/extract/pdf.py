"""Read a PDF's link annotations (with qpdf) and text (with pdftotext), and find URLs split across lines."""

import json
import re
import shutil
import subprocess

# Characters that continue a URL on the next line (ASCII, so not superscript footnote numbers). A single word followed
# by more text is prose, and a line of digits is a footnote or page number.
CONTINUATION = re.compile(r"[\w/?=&%#.~+:-]+", re.ASCII)
PROSE = re.compile(r"[A-Za-z]+[.,;:!]?\s|\d+$")
# A URL-like fragment, or a bare "http", at the end of a line.
TAIL = re.compile(
    r"(?<![\w.-])(?:(?:https?:/*|www\.|(?:[a-z0-9-]+\.)+[a-z]{2,}/|(?:docs|drive)\.google\.)\S*|https?)$",
    re.IGNORECASE,
)


def uris(value):
    """Yield the /URI strings in qpdf's JSON."""
    if isinstance(value, dict):
        for k, v in value.items():
            if k == "/URI" and isinstance(v, str):
                # qpdf prefixes a text string with "u:" and a binary string, in hex, with "b:".
                yield bytes.fromhex(v[2:]).decode("latin-1") if v.startswith("b:") else v.removeprefix("u:")
            else:
                yield from uris(v)
    elif isinstance(value, list):
        for v in value:
            yield from uris(v)


def splits(lines):
    """Return [(fragment, joined)] for each URL-like fragment that ends a line and continues on the next."""
    out = []
    for n, line in enumerate(lines):
        m = TAIL.search(line.rstrip())
        if not m:
            continue
        joined = m.group()
        for following in lines[n + 1 :]:
            following = following.strip()  # noqa: PLW2901
            c = CONTINUATION.match(following)
            if not c or PROSE.match(following):
                break
            joined += c.group()
            # Join the line after, too, only if this line is all URL.
            if c.end() < len(following):
                break
        if joined != m.group():
            out.append((m.group(), joined))
    return out


def read(path, data):
    """Return (text: link targets and body, error, split URLs)."""
    missing = [tool for tool in ("qpdf", "pdftotext") if not shutil.which(tool)]
    if missing:
        return "", f"{' and '.join(missing)} not installed", []
    qpdf = subprocess.run(["qpdf", "--json", "--json-key=qpdf", path], capture_output=True, check=False)
    # By default, pdftotext drops a hyphen that ends a line and joins the lines, which alters a URL.
    pdftotext = subprocess.run(
        ["pdftotext", "-q", "-remove-hyphens", "none", path, "-"],
        capture_output=True,
        check=False,
    )
    try:
        targets = list(uris(json.loads(qpdf.stdout)))
    except ValueError:
        targets = []
    errors = []
    # qpdf exits 3 on warnings, which it recovers from.
    if qpdf.returncode not in (0, 3):
        errors.append(f"qpdf exited {qpdf.returncode}")
    if pdftotext.returncode:
        errors.append(f"pdftotext exited {pdftotext.returncode}")
    if errors and b"/Encrypt" in data:
        errors.append("encrypted")
    body = pdftotext.stdout.decode("utf-8", "ignore")
    return "\n".join([*targets, body]), "; ".join(errors), splits(body.splitlines())
