"""
Read the links in the Docs, Sheets, Slides and Forms APIs' responses and in forms' public pages.

The Docs, Sheets and Slides readers return each link with the API's own range, so that a caller can write it back.
"""

import json
import re

from walkrightin.platforms.google_drive.urls import DRIVE

# The data in a form's public page, with its title, description, questions and options.
FORM_DATA = re.compile(r"FB_PUBLIC_LOAD_DATA_ = (.*?);\s*</script>", re.DOTALL)
# The fields of a form, from the Forms API, that hold no text that respondents see.
FORM_SKIP = {"formId", "itemId", "questionId", "revisionId", "responderUri", "linkedSheetId", "contentUri"}


def strings(value, skip=()):
    """Yield the strings in JSON data, except under the keys in ``skip``."""
    if isinstance(value, str):
        yield value
    elif isinstance(value, list):
        for v in value:
            yield from strings(v, skip)
    elif isinstance(value, dict):
        for k, v in value.items():
            if k not in skip:
                yield from strings(v, skip)


def form_page_texts(html):
    """Return the texts in a form's public page (title, description, questions, options), or None if it has none."""
    m = FORM_DATA.search(html)
    return list(strings(json.loads(m.group(1)))) if m else None


def form_texts(form):
    """Return a form's texts, from the Forms API, as (item ID or "" for the form's own, text)."""
    return [
        (container, "\n".join(strings(value, FORM_SKIP)))
        for container, value in [
            ("", form.get("info", {})),
            *((i.get("itemId", ""), i) for i in form.get("items", [])),
        ]
    ]


def accepting(form):
    """Return whether a form, from the Forms API, accepts responses. The API omits false values."""
    if "publishSettings" not in form:  # a form from before publish settings
        return True
    return form["publishSettings"].get("publishState", {}).get("isAcceptingResponses", False)


def is_google(mime_type):
    return mime_type.startswith("application/vnd.google-apps.")


def doc_links(doc, *, detail=False):
    """
    Return the Doc's links as (range, URL): its text runs', and its smart chips' with a range of None.

    With ``detail``, return (range, URL, part, tab title): part is body, header, footer, footnote, chip or suggestion.
    A suggested link is one in a suggested insertion, or in a suggested change to a run's style.
    """
    found, plain = [], []

    def walk(content, where, part, title):
        for element in content or []:
            if "table" in element:
                for row in element["table"].get("tableRows", []):
                    for cell in row.get("tableCells", []):
                        walk(cell.get("content"), where, part, title)
            for e in element.get("paragraph", {}).get("elements", []):
                run = e.get("textRun", {})
                span = {**where, "startIndex": e.get("startIndex", 0), "endIndex": e.get("endIndex")}
                if url := run.get("textStyle", {}).get("link", {}).get("url"):
                    found.append((span, url, "suggestion" if run.get("suggestedInsertionIds") else part, title))
                    plain.append((span, url))
                elif uri := e.get("richLink", {}).get("richLinkProperties", {}).get("uri"):
                    found.append((None, uri, "chip", title))
                    plain.append((None, uri))
                found.extend(
                    (span, url, "suggestion", title)
                    for change in run.get("suggestedTextStyleChanges", {}).values()
                    if (url := change.get("textStyle", {}).get("link", {}).get("url"))
                )

    def tabs(items):
        for tab in items:
            properties = tab.get("tabProperties", {})
            where = {"tabId": properties.get("tabId")}
            title = properties.get("title", "")
            body = tab.get("documentTab", {})
            walk(body.get("body", {}).get("content"), where, "body", title)
            for kind, part in (("headers", "header"), ("footers", "footer"), ("footnotes", "footnote")):
                for segment_id, segment in body.get(kind, {}).items():
                    walk(segment.get("content"), {**where, "segmentId": segment_id}, part, title)
            tabs(tab.get("childTabs", []))

    tabs(doc.get("tabs", []))
    return found if detail else plain


def column(index):
    """Return a column's letters, from its 0-based index."""
    letters = ""
    index += 1
    while index:
        index, rest = divmod(index - 1, 26)
        letters = chr(65 + rest) + letters
    return letters


def sheet_links(sheet, *, detail=False):
    """
    Return the Sheet's links as (cell, URL): cells', runs' and HYPERLINK formulas', and smart chips' with None.

    With ``detail``, return (cell, URL or None, part, "Tab!A1", visibility, text): part is cell, chip or note. A note,
    and a cell's string value, are returned with a URL of None and their text, for the caller to find URLs in.
    """
    found = []
    for tab in sheet.get("sheets", []):
        properties = tab.get("properties", {})
        sheet_id = properties.get("sheetId", 0)
        title = properties.get("title", "")
        visibility = "hidden" if properties.get("hidden") else "visible"
        for data in tab.get("data", []):
            for r, row in enumerate(data.get("rowData", []), data.get("startRow", 0)):
                for c, value in enumerate(row.get("values", []), data.get("startColumn", 0)):
                    place = f"{title}!{column(c)}{r + 1}"
                    runs = value.get("textFormatRuns", [])
                    urls = [value.get("userEnteredFormat", {}).get("textFormat", {}).get("link", {}).get("uri")]
                    urls += [run.get("format", {}).get("link", {}).get("uri") for run in runs]
                    urls += DRIVE.findall(value.get("userEnteredValue", {}).get("formulaValue", ""))
                    cell = {"sheetId": sheet_id, "row": r, "column": c, "value": value}
                    found += [
                        ({**cell, "url": url}, url, "cell", place, visibility, "")
                        for url in dict.fromkeys(urls)
                        if url
                    ]
                    found += [
                        (None, uri, "chip", place, visibility, "")
                        for run in value.get("chipRuns", [])
                        if (uri := run.get("chip", {}).get("richLinkProperties", {}).get("uri"))
                    ]
                    if note := value.get("note"):
                        found.append((None, None, "note", place, visibility, note))
                    if text := value.get("userEnteredValue", {}).get("stringValue"):
                        found.append((None, None, "cell", place, visibility, text))
    if detail:
        return found
    return [(where, url) for where, url, part, *_ in found if url and part != "note"]


def slide_links(deck, *, detail=False):
    """
    Return the deck's links as (place, URL): text runs', and shapes', lines' and images', on slides and notes.

    With ``detail``, return (place, URL, part, page ID): part is text, shape, line, image, speaker_notes, or layout or
    master for the links on the slides' layouts and masters.
    """
    found = []

    def text(body, where, part, slide):
        for e in body.get("textElements", []):
            if url := e.get("textRun", {}).get("style", {}).get("link", {}).get("url"):
                span = {"type": "FIXED_RANGE", "startIndex": e.get("startIndex", 0), "endIndex": e["endIndex"]}
                found.append(({**where, "kind": "text", "textRange": span}, url, part or "text", slide))

    def walk(elements, part, slide):
        for element in elements or []:
            where = {"objectId": element["objectId"]}
            found.extend(
                ({**where, "kind": kind}, url, part or kind, slide)
                for kind in ("shape", "line", "image")
                if (url := element.get(kind, {}).get(f"{kind}Properties", {}).get("link", {}).get("url"))
            )
            text(element.get("shape", {}).get("text", {}), where, part, slide)
            for row in element.get("table", {}).get("tableRows", []):
                for cell in row.get("tableCells", []):
                    text(cell.get("text", {}), {**where, "cellLocation": cell["location"]}, part, slide)
            walk(element.get("elementGroup", {}).get("children"), part, slide)

    for page in deck.get("slides", []):
        slide = page.get("objectId", "")
        walk(page.get("pageElements"), None, slide)
        walk(page.get("slideProperties", {}).get("notesPage", {}).get("pageElements"), "speaker_notes", slide)
    if detail:
        for part, key in (("layout", "layouts"), ("master", "masters")):
            for page in deck.get(key, []):
                walk(page.get("pageElements"), part, page.get("objectId", ""))
        return found
    return [(where, url) for where, url, *_ in found]
