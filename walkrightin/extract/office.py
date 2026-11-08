"""Read Office and OpenDocument files: their XML, each spreadsheet tab's links, and old binary Office files."""

import html
import re
import zipfile
from pathlib import Path
from urllib.parse import unquote

VISIBILITY = {"visible": "visible", "hidden": "hidden", "veryHidden": "very_hidden"}
ODS = b"application/vnd.oasis.opendocument.spreadsheet"


def contents(path):
    """Return the text of a zip (Office XML, zipped HTML) or another file."""
    if zipfile.is_zipfile(path):
        with zipfile.ZipFile(path) as z:
            return " ".join(
                z.read(name).decode("utf-8", "ignore")
                for name in z.namelist()
                if name.endswith((".xml", ".rels", ".html", ".htm"))
            )
    return Path(path).read_text(errors="ignore")


def legacy(data):
    """Return the text of an Office 97-2003 file: single-byte characters, or UTF-16 at either alignment."""
    return " ".join(
        [data.decode("latin-1"), data.decode("utf-16-le", "ignore"), data[1:].decode("utf-16-le", "ignore")]
    )


def attr(tag, name):
    """Return an XML attribute's raw value, whatever the attributes' order, or None."""
    m = re.search(rf'\b{name}="([^"]*)"', tag)
    return m.group(1) if m else None


def column(n):
    """Return a spreadsheet column's letters, from its 0-based number."""
    letters = ""
    n += 1
    while n:
        n, r = divmod(n - 1, 26)
        letters = chr(65 + r) + letters
    return letters


def strings(markup):
    """Return the text of XML."""
    return html.unescape(re.sub(r"<[^>]+>", "", markup))


def sheet_cells(path):
    """
    Yield (tab, tab visibility, cell, URL, text) for each spreadsheet cell's link, in an XLSX or ODS file.

    The links are HYPERLINK formulas, the cell hyperlink list, and the cell's text (rich-text runs' links aren't in the
    hyperlink list, so URLs typed or linked in a cell's text are found there). Callers find URLs in the last.
    """
    z = zipfile.ZipFile(path)
    names = z.namelist()
    if "mimetype" in names and z.read("mimetype").strip() == ODS:
        yield from ods_cells(z.read("content.xml").decode("utf-8", "ignore"))
        return
    if "xl/workbook.xml" not in names:
        return
    wb = z.read("xl/workbook.xml").decode()
    rels = z.read("xl/_rels/workbook.xml.rels").decode()
    targets = {attr(rel, "Id"): attr(rel, "Target") for rel in re.findall(r"<Relationship\b[^>]*>", rels)}
    shared = []
    if "xl/sharedStrings.xml" in names:
        sst = z.read("xl/sharedStrings.xml").decode("utf-8", "ignore")
        shared = [html.unescape(re.sub(r"<[^>]+>", "", si)) for si in re.findall(r"<si>(.*?)</si>", sst, re.DOTALL)]
    for tag in re.findall(r"<sheet\b[^>]*/>", wb):
        name = html.unescape(attr(tag, "name") or "")
        visibility = VISIBILITY.get(attr(tag, "state") or "visible", "hidden")
        target = targets.get(attr(tag, "r:id"), "").lstrip("/")
        sheet_path = target if target.startswith("xl/") else f"xl/{target}"
        if sheet_path not in names:
            continue
        sheet = z.read(sheet_path).decode("utf-8", "ignore")
        cells = {}
        # Empty cells are self-closing (<c r="A1"/>); match them too, so a cell never runs on into the next one.
        for attrs, body in re.findall(r"<c\b([^>]*?)(?:/>|>(.*?)</c>)", sheet, re.DOTALL):
            ref = attr(attrs, "r")
            if not ref or not body:
                continue
            v = re.search(r"<v>(.*?)</v>", body, re.DOTALL)
            if 't="s"' in attrs and v:
                cells[ref] = shared[int(v.group(1))]
            else:
                inline = re.search(r"<t[^>]*>(.*?)</t>", body, re.DOTALL)
                cells[ref] = html.unescape(inline.group(1)) if inline else ""
            if f := re.search(r"HYPERLINK\(&quot;(.*?)&quot;(?:,\s*&quot;(.*?)&quot;)?", body):
                yield (
                    name,
                    visibility,
                    ref,
                    html.unescape(unquote(f.group(1))),
                    html.unescape(f.group(2) or cells[ref]),
                )
        rels_path = sheet_path.replace("worksheets/", "worksheets/_rels/") + ".rels"
        hyper = {}
        if rels_path in names:
            rels_xml = z.read(rels_path).decode("utf-8", "ignore")
            hyper = {attr(rel, "Id"): attr(rel, "Target") for rel in re.findall(r"<Relationship\b[^>]*>", rels_xml)}
        # Attributes come in any order: Google writes r:id before ref.
        for hyperlink in re.findall(r"<hyperlink\b[^>]*>", sheet):
            ref, rid = attr(hyperlink, "ref"), attr(hyperlink, "r:id")
            if ref and rid and hyper.get(rid):
                yield name, visibility, ref, html.unescape(unquote(hyper[rid])), cells.get(ref, "")
        for ref, text in cells.items():
            yield name, visibility, ref, None, text


def ods_cells(content):
    """Yield (tab, tab visibility, cell, URL, text) for each link in an ODS file's content.xml."""
    hidden = {
        attr(attrs, "style:name")
        for attrs, body in re.findall(r"<style:style\b([^>]*)>(.*?)</style:style>", content, re.DOTALL)
        if 'table:display="false"' in body
    }
    for attrs, table in re.findall(r"<table:table\s([^>]*)>(.*?)</table:table>", content, re.DOTALL):
        name = html.unescape(attr(attrs, "table:name") or "")
        visibility = "hidden" if attr(attrs, "table:style-name") in hidden else "visible"
        row = 0
        for row_attrs, cells in re.findall(
            r"<table:table-row\b([^>]*?)(?:/>|>(.*?)</table:table-row>)", table, re.DOTALL
        ):
            col = 0
            # Covered cells (under a merged cell) take a column, like other cells.
            for cell_attrs, body in re.findall(
                r"<table:(?:covered-)?table-cell\b([^>]*?)(?:/>|>(.*?)</table:(?:covered-)?table-cell>)",
                cells,
                re.DOTALL,
            ):
                ref = f"{column(col)}{row + 1}"
                col += int(attr(cell_attrs, "table:number-columns-repeated") or 1)
                formula = html.unescape(attr(cell_attrs, "table:formula") or "")
                if not body and not formula:
                    continue
                text = "\n".join(strings(p) for p in re.findall(r"<text:p\b[^>]*>(.*?)</text:p>", body, re.DOTALL))
                if f := re.search(r'HYPERLINK\("(.*?)"(?:\s*;\s*"(.*?)")?', formula):
                    yield name, visibility, ref, unquote(f.group(1)), f.group(2) or text
                for href, label in re.findall(
                    r'<text:a\b[^>]*?xlink:href="([^"]*)"[^>]*>(.*?)</text:a>', body, re.DOTALL
                ):
                    yield name, visibility, ref, html.unescape(unquote(href)), strings(label)
                yield name, visibility, ref, None, text
            row += int(attr(row_attrs, "table:number-rows-repeated") or 1)
