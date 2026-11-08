import json
import zipfile

from walkrightin.extract import CHECK, JOINED, TRUNCATED, Extractor

from .conftest import DOC, FILE, SHEET, needs_pdf_tools, pdf, xlsx

DOC_URL = f"https://docs.google.com/document/d/{DOC}/edit"
SHEET_URL = f"https://docs.google.com/spreadsheets/d/{SHEET}/edit"
FILE_URL = f"https://drive.google.com/file/d/{FILE}/view"


def by_url(links):
    return {link.url: link for link in links}


def test_text_wrapped_and_short(extractor):
    text = f"See https://www.google.com/url?q={DOC_URL}&amp;sa=D and bit.ly/doc. Also microsoft.co/doc and t.co/abc."
    links = by_url(extractor.text(text))

    assert set(links) == {DOC_URL, "https://bit.ly/doc", "https://t.co/abc"}


def test_text_truncated_and_templates(extractor):
    text = 'https://docs.google.com/document/d/1Abc... and "https://docs.google.com/document/d/" + id'

    assert extractor.text(text) == []


def test_text_percent_encoded(extractor):
    text = f"go to https%3A%2F%2Fdocs.google.com%2Fdocument%2Fd%2F{DOC}%2Fedit now"

    assert [link.url for link in extractor.text(text)] == [DOC_URL]


def test_text_json_escapes(extractor):
    text = DOC_URL.replace("/", "\\/").replace("-", "\\u002D")

    assert [link.url for link in extractor.text(text)] == [DOC_URL]


def test_html_parts(extractor):
    text = f"""<p><a href="{DOC_URL}">The doc</a>
    <a href="{FILE_URL}"><img src="cover.png" alt="Cover"></a>
    <!-- {SHEET_URL} -->
    <iframe src="https://docs.google.com/presentation/d/{DOC}/embed"></iframe>
    <div data-url="https://drive.google.com/drive/folders/{FILE}"></div>
    <script>var u = "https:\\/\\/docs.google.com\\/document\\/d\\/{SHEET}\\/edit";</script>
    Plain https://drive.google.com/open?id={DOC}</p>"""
    links = by_url(extractor.text(text, "html"))

    assert links[DOC_URL].text == "The doc"
    assert (links[DOC_URL].part, links[DOC_URL].visibility) == ("body", "visible")
    assert links[FILE_URL].text == "Cover"
    assert (links[SHEET_URL].part, links[SHEET_URL].visibility) == ("html_comment", "source_only")
    embed = links[f"https://docs.google.com/presentation/d/{DOC}/embed"]
    assert (embed.part, embed.visibility) == ("attribute:src", "visible")
    data = links[f"https://drive.google.com/drive/folders/{FILE}"]
    assert (data.part, data.visibility) == ("attribute:data-url", "source_only")
    script = links[f"https://docs.google.com/document/d/{SHEET}/edit"]
    assert (script.part, script.visibility) == ("script", "source_only")
    assert links[f"https://drive.google.com/open?id={DOC}"].part == "body"


def test_html_percent_encoded_href(extractor):
    links = extractor.text(f'<a href="{DOC_URL}?fbclid%3Dabc">Doc</a>', "html")

    assert [(link.url, link.text) for link in links] == [(f"{DOC_URL}?fbclid%3Dabc", "Doc")]


def test_html_wbr(extractor):
    links = extractor.text('<a href="https://bit.ly/do<wbr />c">https://bit.ly/do<wbr />c</a>', "html")

    assert [(link.url, link.text) for link in links] == [("https://bit.ly/doc", "https://bit.ly/doc")]


def test_markdown(extractor):
    text = f"""[Report [2020]]({DOC_URL}) and [broken]({FILE_URL} and <{SHEET_URL}>

[ref]: https://drive.google.com/drive/folders/{FILE}
[a reference][ref]
"""
    links = by_url(extractor.text(text, "markdown"))

    assert links[DOC_URL].text == "Report [2020]"
    assert links[FILE_URL].text == "broken"
    assert links[f"https://drive.google.com/drive/folders/{FILE}"].text == "a reference"
    assert links[SHEET_URL].text == SHEET_URL


def test_rst(extractor):
    links = extractor.text(f"See `the doc <{DOC_URL}>`_.", "rst")

    assert [(link.url, link.text) for link in links] == [(DOC_URL, "the doc")]


def test_bare_ids(shortlinks):
    linked = f"https://drive.google.com/file/d/{FILE[:33]}/view"
    text = f'sheet = "{DOC[:33]}"\nurl = "{linked}"\nid = "{FILE[:33]}"'

    links = Extractor(shortlinks, bare_ids=True).text(text)

    assert [(link.url, link.part) for link in links] == [
        (linked, "body"),
        (f"https://drive.google.com/file/d/{DOC[:33]}/view", "bare_id"),
    ]


def test_bare_ids_off(extractor):
    assert extractor.text(f'id = "{DOC[:33]}"') == []


def test_docx(extractor, tmp_path):
    path = tmp_path / "file.docx"
    with zipfile.ZipFile(path, "w") as z:
        z.writestr("word/document.xml", "<w:document><w:t>Hello bit.ly/doc</w:t></w:document>")
        z.writestr(
            "word/_rels/document.xml.rels",
            f'<Relationships><Relationship Id="rId1" Target="{FILE_URL}" TargetMode="External"/></Relationships>',
        )

    links, error = extractor.file(str(path))

    assert error == ""
    assert {link.url for link in links} == {FILE_URL, "https://bit.ly/doc"}


def test_xlsx_tabs(extractor, tmp_path):
    path = tmp_path / "file.xlsx"
    xlsx(
        path,
        [
            ("Visible", None, [("A1", "Formula", DOC_URL, None), ("B1", f"typed {FILE_URL}", None, None)]),
            ("Secret", "hidden", [("A1", "Linked", None, SHEET_URL)]),
            ("Vault", "veryHidden", [("C3", f"{FILE_URL}", None, None)]),
        ],
    )

    links, error = extractor.file(str(path))
    cells = {(link.url, link.container): link for link in links if link.part == "cell"}

    assert error == ""
    assert cells[(DOC_URL, "Visible!A1")].text == "Formula"
    assert cells[(DOC_URL, "Visible!A1")].visibility == "visible"
    assert cells[(FILE_URL, "Visible!B1")].text == ""
    assert cells[(SHEET_URL, "Secret!A1")].visibility == "hidden"
    assert cells[(SHEET_URL, "Secret!A1")].text == "Linked"
    assert cells[(FILE_URL, "Vault!C3")].visibility == "very_hidden"
    # The raw pass over the XML finds every URL too.
    assert {link.url for link in links if link.part == "body"} == {DOC_URL, FILE_URL, SHEET_URL}


def odf(path, mimetype, content):
    with zipfile.ZipFile(path, "w") as z:
        z.writestr("mimetype", mimetype)
        z.writestr("content.xml", f"<office:document-content>{content}</office:document-content>")


def test_odt(extractor, tmp_path):
    path = tmp_path / "file.odt"
    odf(
        path,
        "application/vnd.oasis.opendocument.text",
        f'<text:p>See <text:a xlink:type="simple" xlink:href="{DOC_URL}"><text:span>the doc</text:span></text:a>'
        " and bit.ly/doc</text:p>",
    )

    links, error = extractor.file(str(path))

    assert error == ""
    assert {(link.url, link.text) for link in links} == {(DOC_URL, "the doc"), ("https://bit.ly/doc", "")}


def test_ods_tabs(extractor, tmp_path):
    path = tmp_path / "file.ods"
    odf(
        path,
        "application/vnd.oasis.opendocument.spreadsheet",
        '<office:automatic-styles><style:style style:name="ta1" style:family="table">'
        '<style:table-properties table:display="true"/></style:style>'
        '<style:style style:name="ta2" style:family="table"><style:table-properties table:display="false"/>'
        "</style:style></office:automatic-styles>"
        '<table:table table:name="Visible" table:style-name="ta1"><table:table-column/>'
        '<table:table-row table:number-rows-repeated="2"><table:table-cell/></table:table-row>'
        '<table:table-row><table:table-cell table:number-columns-repeated="2"/>'
        f'<table:table-cell table:formula="of:=HYPERLINK(&quot;{DOC_URL}&quot;;&quot;Formula&quot;)">'
        "<text:p>Formula</text:p></table:table-cell>"
        f"<table:table-cell><text:p>typed {FILE_URL}</text:p></table:table-cell></table:table-row></table:table>"
        '<table:table table:name="Secret" table:style-name="ta2"><table:table-row>'
        f'<table:table-cell><text:p><text:a xlink:href="{SHEET_URL}">Linked</text:a></text:p></table:table-cell>'
        "</table:table-row></table:table>",
    )

    links, error = extractor.file(str(path))
    cells = {(link.url, link.container): link for link in links if link.part == "cell"}

    assert error == ""
    assert set(cells) == {(DOC_URL, "Visible!C3"), (FILE_URL, "Visible!D3"), (SHEET_URL, "Secret!A1")}
    assert cells[(DOC_URL, "Visible!C3")].text == "Formula"
    assert cells[(DOC_URL, "Visible!C3")].visibility == "visible"
    assert cells[(SHEET_URL, "Secret!A1")].text == "Linked"
    assert cells[(SHEET_URL, "Secret!A1")].visibility == "hidden"


def test_legacy_office(extractor, tmp_path):
    path = tmp_path / "file.doc"
    path.write_bytes(b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1" + b"\0" * 8 + f"see {DOC_URL} ".encode("utf-16-le"))

    links, error = extractor.file(str(path))

    assert error == ""
    assert [link.url for link in links] == [DOC_URL]


@needs_pdf_tools
def test_pdf_annotation_and_split(extractor, tmp_path):
    path = tmp_path / "file.pdf"
    path.write_bytes(pdf([f"Annotated. Split: https://docs.google.com/document/d/{DOC[:20]}", DOC[20:]], [FILE_URL]))

    links, error = extractor.file(str(path))
    links = by_url(links)

    assert error == ""
    assert links[FILE_URL].note == ""
    assert links[f"https://docs.google.com/document/d/{DOC}"].note.startswith(JOINED)
    assert links[f"https://docs.google.com/document/d/{DOC[:20]}"].note.startswith(f"{TRUNCATED} of ")


@needs_pdf_tools
def test_pdf_check_by_hand(extractor, tmp_path):
    path = tmp_path / "file.pdf"
    path.write_bytes(pdf(["Visit https://", "example.com/page"]))

    links, _ = extractor.file(str(path))

    assert [link.note for link in links] == [f"{CHECK}: 'https://' ends a line"]


def test_pdf_tools_missing(extractor, tmp_path, monkeypatch):
    monkeypatch.setattr("walkrightin.extract.pdf.shutil.which", lambda _: None)
    path = tmp_path / "file.pdf"
    path.write_bytes(b"%PDF-1.4\n")

    links, error = extractor.file(str(path))

    assert links == []
    assert error == "qpdf and pdftotext not installed"


def test_notebook(extractor, tmp_path):
    path = tmp_path / "a.ipynb"
    cells = [
        {
            "cell_type": "markdown",
            "source": ["See [the data](", f"https://docs.google.com/spreadsheets/d/{SHEET}/edit)"],
        },
        {
            "cell_type": "code",
            "source": f"open('https://drive.google.com/file/d/{FILE}/view')",
            "outputs": [
                {"output_type": "stream", "text": ["https://bit.ly/doc\n"]},
                {
                    "output_type": "display_data",
                    "data": {
                        "image/png": "iVBOR",
                        "text/html": [f"<a href='https://drive.google.com/open?id={DOC}'>x</a>"],
                    },
                },
            ],
        },
    ]
    path.write_text(json.dumps({"cells": cells}))

    links, error = extractor.file(path)

    assert error == ""
    assert [(link.url[-20:], link.text, link.part, link.container) for link in links] == [
        (f"{SHEET[-15:]}/edit", "the data", "body", "cell 1"),
        (f"{FILE[-15:]}/view", "", "body", "cell 2"),
        ("https://bit.ly/doc", "", "output", "cell 2"),
        (f"open?id={DOC}"[-20:], "x", "output", "cell 2"),
    ]


def test_notebook_invalid(extractor, tmp_path):
    path = tmp_path / "a.ipynb"
    path.write_text(f"not json https://drive.google.com/file/d/{FILE}/view")

    assert [link.url for link in extractor.file(path)[0]] == [f"https://drive.google.com/file/d/{FILE}/view"]
