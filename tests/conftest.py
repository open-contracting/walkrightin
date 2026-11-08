import shutil
import zipfile

import pytest

from walkrightin.extract import Extractor
from walkrightin.shortlinks import ShortLinks

DOC = "1AbCdEfGhIjKlMnOpQrStUvWxYz0123456789abcdEF"
SHEET = "1QwErTyUiOpAsDfGhJkLzXcVbNm1234567890abcd"
FILE = "1ZyXwVuTsRqPoNmLkJiHgFeDcBa9876543210zyxw"

needs_pdf_tools = pytest.mark.skipif(
    not (shutil.which("qpdf") and shutil.which("pdftotext")), reason="qpdf and pdftotext are required"
)


class FakeShortLinks(ShortLinks):
    """Short links that resolve from a dict, without requests."""

    def __init__(self, targets=None, **kwargs):
        super().__init__(**kwargs)
        self.targets = targets or {}

    def _follow(self, url):
        return self.targets.get(url), "301", self.targets.get(url) or url


@pytest.fixture
def shortlinks():
    return FakeShortLinks({"https://bit.ly/doc": f"https://docs.google.com/document/d/{DOC}/edit"})


@pytest.fixture
def extractor(shortlinks):
    return Extractor(shortlinks)


def pdf(text_lines, uris=()):
    """Return a minimal PDF with lines of text in Helvetica and link annotations to the URIs."""
    stream = "BT /F1 10 Tf 12 TL 20 800 Td " + " ".join(f"({line}) Tj T*" for line in text_lines) + " ET"
    annots = " ".join(f"{6 + i} 0 R" for i in range(len(uris)))
    objects = [
        "<< /Type /Catalog /Pages 2 0 R >>",
        "<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        (
            "<< /Type /Page /Parent 2 0 R /MediaBox [0 0 595 842] /Contents 4 0 R "
            f"/Resources << /Font << /F1 5 0 R >> >> /Annots [{annots}] >>"
        ),
        f"<< /Length {len(stream)} >>\nstream\n{stream}\nendstream",
        "<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
        *(f"<< /Type /Annot /Subtype /Link /Rect [0 0 10 10] /A << /S /URI /URI ({uri}) >> >>" for uri in uris),
    ]
    out = b"%PDF-1.4\n"
    offsets = []
    for n, body in enumerate(objects, 1):
        offsets.append(len(out))
        out += f"{n} 0 obj\n{body}\nendobj\n".encode()
    xref = len(out)
    out += f"xref\n0 {len(objects) + 1}\n0000000000 65535 f \n".encode()
    out += b"".join(f"{o:010d} 00000 n \n".encode() for o in offsets)
    out += f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF\n".encode()
    return out


def xlsx(path, sheets):
    """Write an XLSX with sheets as (name, state, cells), each cell as (ref, text, formula URL, hyperlink URL)."""
    with zipfile.ZipFile(path, "w") as z:
        entries = "".join(
            f'<sheet name="{name}" sheetId="{n}"{f" state={chr(34)}{state}{chr(34)}" if state else ""} r:id="rId{n}"/>'
            for n, (name, state, _) in enumerate(sheets, 1)
        )
        z.writestr("xl/workbook.xml", f"<workbook><sheets>{entries}</sheets></workbook>")
        rels = "".join(
            f'<Relationship Id="rId{n}" Target="worksheets/sheet{n}.xml"/>' for n in range(1, len(sheets) + 1)
        )
        z.writestr("xl/_rels/workbook.xml.rels", f"<Relationships>{rels}</Relationships>")
        for n, (_, _, cells) in enumerate(sheets, 1):
            body, hyperlinks, sheet_rels = "", "", ""
            for i, (ref, text, formula, hyperlink) in enumerate(cells):
                if formula:
                    body += f'<c r="{ref}" t="str"><f>HYPERLINK(&quot;{formula}&quot;,&quot;{text}&quot;)</f></c>'
                else:
                    body += f'<c r="{ref}" t="inlineStr"><is><t>{text}</t></is></c>'
                if hyperlink:
                    hyperlinks += f'<hyperlink r:id="rIdH{i}" ref="{ref}"/>'
                    sheet_rels += f'<Relationship Id="rIdH{i}" Target="{hyperlink}" TargetMode="External"/>'
            z.writestr(
                f"xl/worksheets/sheet{n}.xml",
                f"<worksheet><sheetData><row>{body}</row></sheetData><hyperlinks>{hyperlinks}</hyperlinks></worksheet>",
            )
            z.writestr(f"xl/worksheets/_rels/sheet{n}.xml.rels", f"<Relationships>{sheet_rels}</Relationships>")
