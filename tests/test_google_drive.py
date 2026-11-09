import pytest

from walkrightin.extract import Extractor
from walkrightin.platforms import Context
from walkrightin.platforms.google_drive import GoogleDrive, Options, client, readers
from walkrightin.platforms.google_drive.urls import DOCUMENT, PRESENTATION, SPREADSHEET

from .conftest import DOC as DOC_ID
from .conftest import FILE, FakeShortLinks
from .conftest import SHEET as SHEET_ID

DOC = {
    "tabs": [
        {
            "tabProperties": {"tabId": "t.0", "title": "Main"},
            "documentTab": {
                "body": {
                    "content": [
                        {
                            "paragraph": {
                                "elements": [
                                    {"startIndex": 1, "endIndex": 5, "textRun": {"textStyle": {"link": {"url": "A"}}}},
                                    {"richLink": {"richLinkProperties": {"uri": "CHIP"}}},
                                    {
                                        "startIndex": 5,
                                        "endIndex": 9,
                                        "textRun": {
                                            "textStyle": {"link": {"url": "S"}},
                                            "suggestedInsertionIds": ["s1"],
                                        },
                                    },
                                    {
                                        "startIndex": 9,
                                        "endIndex": 12,
                                        "textRun": {
                                            "textStyle": {},
                                            "suggestedTextStyleChanges": {
                                                "s2": {"textStyle": {"link": {"url": "STYLE"}}}
                                            },
                                        },
                                    },
                                ]
                            }
                        }
                    ]
                },
                "footnotes": {
                    "f1": {
                        "content": [
                            {
                                "paragraph": {
                                    "elements": [
                                        {
                                            "startIndex": 0,
                                            "endIndex": 2,
                                            "textRun": {"textStyle": {"link": {"url": "F"}}},
                                        }
                                    ]
                                }
                            }
                        ]
                    }
                },
            },
            "childTabs": [
                {
                    "tabProperties": {"tabId": "t.1", "title": "Child"},
                    "documentTab": {
                        "body": {
                            "content": [
                                {
                                    "table": {
                                        "tableRows": [
                                            {
                                                "tableCells": [
                                                    {
                                                        "content": [
                                                            {
                                                                "paragraph": {
                                                                    "elements": [
                                                                        {
                                                                            "startIndex": 3,
                                                                            "endIndex": 4,
                                                                            "textRun": {
                                                                                "textStyle": {"link": {"url": "T"}}
                                                                            },
                                                                        }
                                                                    ]
                                                                }
                                                            }
                                                        ]
                                                    }
                                                ]
                                            }
                                        ]
                                    }
                                }
                            ]
                        }
                    },
                }
            ],
        }
    ]
}


def test_doc_links_plain():
    # As drive-sharing's repoint consumes them: ranges for text runs, None for chips.
    assert readers.doc_links(DOC) == [
        ({"tabId": "t.0", "startIndex": 1, "endIndex": 5}, "A"),
        (None, "CHIP"),
        ({"tabId": "t.0", "startIndex": 5, "endIndex": 9}, "S"),
        ({"tabId": "t.0", "segmentId": "f1", "startIndex": 0, "endIndex": 2}, "F"),
        ({"tabId": "t.1", "startIndex": 3, "endIndex": 4}, "T"),
    ]


def test_doc_links_detail():
    assert [(url, part, title) for _, url, part, title, _ in readers.doc_links(DOC, detail=True)] == [
        ("A", "body", "Main"),
        ("CHIP", "chip", "Main"),
        ("S", "suggestion", "Main"),
        ("STYLE", "suggestion", "Main"),
        ("F", "footnote", "Main"),
        ("T", "body", "Child"),
    ]


SHEET = {
    "sheets": [
        {
            "properties": {"sheetId": 7, "title": "Data", "hidden": True},
            "data": [
                {
                    "startRow": 1,
                    "startColumn": 26,
                    "rowData": [
                        {
                            "values": [
                                {
                                    "userEnteredFormat": {"textFormat": {"link": {"uri": "CELL"}}},
                                    "textFormatRuns": [{"format": {"link": {"uri": "RUN"}}}],
                                    "note": "see NOTE",
                                },
                                {
                                    "userEnteredValue": {
                                        "formulaValue": '=HYPERLINK("https://docs.google.com/document/d/x/edit","t")'
                                    },
                                    "chipRuns": [{"chip": {"richLinkProperties": {"uri": "CHIP"}}}],
                                },
                                {"userEnteredValue": {"stringValue": "typed URL"}},
                            ]
                        }
                    ],
                }
            ],
        }
    ]
}


def test_sheet_links_plain():
    assert [
        (where and (where["sheetId"], where["row"], where["column"]), url) for where, url in readers.sheet_links(SHEET)
    ] == [
        ((7, 1, 26), "CELL"),
        ((7, 1, 26), "RUN"),
        ((7, 1, 27), "https://docs.google.com/document/d/x/edit"),
        (None, "CHIP"),
    ]


def test_sheet_links_detail():
    assert [row[1:] for row in readers.sheet_links(SHEET, detail=True)] == [
        ("CELL", "cell", "Data!AA2", "hidden", ""),
        ("RUN", "cell", "Data!AA2", "hidden", ""),
        (None, "note", "Data!AA2", "hidden", "see NOTE"),
        (
            "https://docs.google.com/document/d/x/edit",
            "cell",
            "Data!AB2",
            "hidden",
            '=HYPERLINK("https://docs.google.com/document/d/x/edit","t")',
        ),
        ("CHIP", "chip", "Data!AB2", "hidden", ""),
        (None, "cell", "Data!AC2", "hidden", "typed URL"),
    ]


def test_column():
    assert [readers.column(i) for i in (0, 25, 26, 701, 702)] == ["A", "Z", "AA", "ZZ", "AAA"]


DECK = {
    "slides": [
        {
            "objectId": "s1",
            "pageElements": [
                {
                    "objectId": "e1",
                    "shape": {
                        "shapeProperties": {"link": {"url": "SHAPE"}},
                        "text": {"textElements": [{"endIndex": 3, "textRun": {"style": {"link": {"url": "TEXT"}}}}]},
                    },
                },
                {"objectId": "e2", "image": {"imageProperties": {"link": {"url": "IMAGE"}}}},
                {
                    "objectId": "g1",
                    "elementGroup": {
                        "children": [{"objectId": "e3", "line": {"lineProperties": {"link": {"url": "LINE"}}}}]
                    },
                },
            ],
            "slideProperties": {
                "notesPage": {
                    "pageElements": [
                        {
                            "objectId": "n1",
                            "shape": {
                                "text": {
                                    "textElements": [{"endIndex": 2, "textRun": {"style": {"link": {"url": "NOTE"}}}}]
                                }
                            },
                        }
                    ]
                }
            },
        }
    ],
    "layouts": [
        {
            "objectId": "l1",
            "pageElements": [{"objectId": "e4", "shape": {"shapeProperties": {"link": {"url": "LAYOUT"}}}}],
        }
    ],
    "masters": [
        {
            "objectId": "m1",
            "pageElements": [{"objectId": "e5", "image": {"imageProperties": {"link": {"url": "MASTER"}}}}],
        }
    ],
}


def test_slide_links():
    assert [url for _, url in readers.slide_links(DECK)] == ["SHAPE", "TEXT", "IMAGE", "LINE", "NOTE"]
    assert [(url, part, slide) for _, url, part, slide, _ in readers.slide_links(DECK, detail=True)] == [
        ("SHAPE", "shape", "s1"),
        ("TEXT", "text", "s1"),
        ("IMAGE", "image", "s1"),
        ("LINE", "line", "s1"),
        ("NOTE", "speaker_notes", "s1"),
        ("LAYOUT", "layout", "l1"),
        ("MASTER", "master", "m1"),
    ]


def test_form_page_texts():
    data = '[null, ["Intro", [[1, "Q1", null, 0, [[2, [["A"]]]]]]], "/forms"]'
    html = f"<script>var FB_PUBLIC_LOAD_DATA_ = {data};</script>"

    assert readers.form_page_texts(html) == ["Intro", "Q1", "A", "/forms"]
    assert readers.form_page_texts("<html></html>") is None


def test_form_texts():
    form = {
        "formId": "F",
        "responderUri": "https://docs.google.com/forms/d/e/P/viewform",
        "info": {"title": "T", "description": "D"},
        "items": [
            {
                "itemId": "q1",
                "title": "Q",
                "questionItem": {"question": {"questionId": "x", "choiceQuestion": {"options": [{"value": "O"}]}}},
            }
        ],
    }

    assert readers.form_texts(form) == [("", "T\nD"), ("q1", "Q\nO")]


@pytest.mark.parametrize(
    ("form", "expected"),
    [
        ({}, True),
        ({"publishSettings": {"publishState": {"isPublished": True, "isAcceptingResponses": True}}}, True),
        ({"publishSettings": {"publishState": {"isPublished": True}}}, False),
    ],
)
def test_accepting(form, expected):
    assert readers.accepting(form) is expected


def test_users_for():
    g = client.Client("key.json", ["a@example.org", "b@example.org"], "example.org")

    assert g.users_for("owner@example.org") == ["owner@example.org", "a@example.org", "b@example.org"]
    assert g.users_for("someone@elsewhere.org") == ["a@example.org", "b@example.org"]
    assert g.users_for("a@example.org") == ["a@example.org", "b@example.org"]


def test_disposition_ext():
    assert client.disposition_ext('attachment; filename="Report 2020.PDF"') == "pdf"
    assert client.disposition_ext("attachment; filename*=UTF-8''r%C3%A9sum%C3%A9.docx") == "docx"
    assert client.disposition_ext("") == ""


def test_link(monkeypatch):
    responses = {
        "unreadable": [],
        "none": [{"id": "1", "type": "user", "role": "writer"}],
        "my drive": [{"id": "anyoneWithLink", "type": "anyone", "role": "reader"}],
        "own": [
            {
                "type": "anyone",
                "role": "writer",
                "permissionDetails": [{"inherited": True, "inheritedFrom": "F"}, {"inherited": False}],
            }
        ],
        "unknown folder": [{"type": "anyone", "role": "reader", "permissionDetails": [{"inherited": True}]}],
        "inherited": [
            {"type": "anyone", "role": "reader", "permissionDetails": [{"inherited": True, "inheritedFrom": "F"}]}
        ],
    }
    monkeypatch.setattr(
        client.Client, "pages", lambda _self, _user, url, _key, _params: (responses[url.split("/")[-2]], "")
    )
    g = client.Client()

    assert {key: g.link("user", key) for key in responses} == {
        "unreadable": ("?", False, ""),
        "none": ("", False, ""),
        "my drive": ("reader", False, ""),
        "own": ("writer", False, ""),
        "unknown folder": ("reader", True, ""),
        "inherited": ("reader", True, "F"),
    }


class Response:
    def __init__(self, status_code, body):
        self.status_code = status_code
        self.ok = status_code == 200
        self.reason = "Forbidden"
        self.body = body

    def json(self):
        return self.body


def rate_limit(reason):
    return Response(403, {"error": {"errors": [{"domain": "usageLimits", "reason": reason}]}})


def test_rate_limited():
    assert client.rate_limited(rate_limit("userRateLimitExceeded"))
    assert not client.rate_limited(rate_limit("insufficientFilePermissions"))
    assert not client.rate_limited(Response(403, None))
    assert not client.rate_limited(Response(200, {}))


@pytest.mark.parametrize(
    ("responses", "expected", "sleeps"),
    [
        ([rate_limit("rateLimitExceeded"), Response(200, {"id": "1"})], ({"id": "1"}, ""), 1),
        ([rate_limit("insufficientFilePermissions")], (None, "403 Forbidden"), 0),
        ([rate_limit("rateLimitExceeded")] * 6, (None, "403 Forbidden"), 5),
    ],
)
def test_get_rate_limited(monkeypatch, responses, expected, sleeps):
    remaining = list(responses)
    slept = []
    session = type("Session", (), {"get": lambda *_args, **_kwargs: remaining.pop(0)})()
    monkeypatch.setattr(client.Client, "session", lambda _self, _user: session)
    monkeypatch.setattr(client.time, "sleep", slept.append)

    assert client.Client().get("user", "https://www.googleapis.com/drive/v3/files/1") == expected
    assert len(slept) == sleeps


def test_api_links_blank():
    doc_url = f"https://docs.google.com/document/d/{DOC_ID}/edit"
    file_url = f"https://drive.google.com/file/d/{FILE}/view"
    sheet_url = f"https://docs.google.com/spreadsheets/d/{SHEET_ID}/edit"

    def run(content, url):
        return {"textRun": {"content": content, "textStyle": {"link": {"url": url}}}}

    doc = {
        "tabs": [
            {
                "tabProperties": {"tabId": "t.0", "title": "Main"},
                "documentTab": {
                    "body": {
                        "content": [
                            {
                                "paragraph": {
                                    "elements": [
                                        run(" ", doc_url),
                                        run("The ", file_url),
                                        run(" ", file_url),
                                        {"richLink": {"richLinkProperties": {"uri": sheet_url}}},
                                    ]
                                }
                            }
                        ]
                    }
                },
            }
        ]
    }
    sheet = {
        "sheets": [
            {
                "properties": {"sheetId": 0, "title": "Data"},
                "data": [
                    {
                        "rowData": [
                            {
                                "values": [
                                    {
                                        "userEnteredValue": {"stringValue": " "},
                                        "userEnteredFormat": {"textFormat": {"link": {"uri": doc_url}}},
                                    },
                                    {
                                        "userEnteredValue": {"stringValue": "See  "},
                                        "textFormatRuns": [
                                            {"startIndex": 0, "format": {}},
                                            {"startIndex": 4, "format": {"link": {"uri": file_url}}},
                                        ],
                                    },
                                ]
                            }
                        ]
                    }
                ],
            }
        ]
    }
    deck = {
        "slides": [
            {
                "objectId": "s1",
                "pageElements": [
                    {
                        "objectId": "b1",
                        "shape": {
                            "shapeProperties": {"link": {"url": sheet_url}},
                            "text": {
                                "textElements": [
                                    {"endIndex": 1, "textRun": {"content": " ", "style": {"link": {"url": doc_url}}}},
                                    {
                                        "endIndex": 2,
                                        "textRun": {"content": " ", "style": {"link": {"url": sheet_url}}},
                                    },
                                ]
                            },
                        },
                    }
                ],
            }
        ]
    }
    gd = GoogleDrive(Options(), Context(extractor=Extractor(FakeShortLinks())))
    gd.client.document = lambda _user, _id: (doc, "")
    gd.client.spreadsheet = lambda _user, _id: (sheet, "")
    gd.client.presentation = lambda _user, _id: (deck, "")

    def seen(mime):
        links, error = gd.api_links("user", "id", mime)
        assert error == ""
        return {link.url: link.visibility for link in links}

    # A link is blank only if none of its runs has text, and a chip or a shape shows it.
    assert seen(DOCUMENT) == {doc_url: "blank", file_url: "visible", sheet_url: "visible"}
    assert seen(SPREADSHEET) == {doc_url: "blank", file_url: "blank"}
    assert seen(PRESENTATION) == {doc_url: "blank", sheet_url: "visible"}
