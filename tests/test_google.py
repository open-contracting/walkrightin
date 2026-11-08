from walkrightin import google

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
    assert google.doc_links(DOC) == [
        ({"tabId": "t.0", "startIndex": 1, "endIndex": 5}, "A"),
        (None, "CHIP"),
        ({"tabId": "t.0", "startIndex": 5, "endIndex": 9}, "S"),
        ({"tabId": "t.0", "segmentId": "f1", "startIndex": 0, "endIndex": 2}, "F"),
        ({"tabId": "t.1", "startIndex": 3, "endIndex": 4}, "T"),
    ]


def test_doc_links_detail():
    assert [(url, part, title) for _, url, part, title in google.doc_links(DOC, detail=True)] == [
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
        (where and (where["sheetId"], where["row"], where["column"]), url) for where, url in google.sheet_links(SHEET)
    ] == [
        ((7, 1, 26), "CELL"),
        ((7, 1, 26), "RUN"),
        ((7, 1, 27), "https://docs.google.com/document/d/x/edit"),
        (None, "CHIP"),
    ]


def test_sheet_links_detail():
    assert [row[1:] for row in google.sheet_links(SHEET, detail=True)] == [
        ("CELL", "cell", "Data!AA2", "hidden", ""),
        ("RUN", "cell", "Data!AA2", "hidden", ""),
        (None, "note", "Data!AA2", "hidden", "see NOTE"),
        ("https://docs.google.com/document/d/x/edit", "cell", "Data!AB2", "hidden", ""),
        ("CHIP", "chip", "Data!AB2", "hidden", ""),
        (None, "cell", "Data!AC2", "hidden", "typed URL"),
    ]


def test_column():
    assert [google.column(i) for i in (0, 25, 26, 701, 702)] == ["A", "Z", "AA", "ZZ", "AAA"]


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
    ]
}


def test_slide_links():
    assert [url for _, url in google.slide_links(DECK)] == ["SHAPE", "TEXT", "IMAGE", "LINE", "NOTE"]
    assert [(url, part, slide) for _, url, part, slide in google.slide_links(DECK, detail=True)] == [
        ("SHAPE", "shape", "s1"),
        ("TEXT", "text", "s1"),
        ("IMAGE", "image", "s1"),
        ("LINE", "line", "s1"),
        ("NOTE", "speaker_notes", "s1"),
    ]


def test_users_for():
    g = google.Google("key.json", ["a@example.org", "b@example.org"], "example.org")

    assert g.users_for("owner@example.org") == ["owner@example.org", "a@example.org", "b@example.org"]
    assert g.users_for("someone@elsewhere.org") == ["a@example.org", "b@example.org"]
    assert g.users_for("a@example.org") == ["a@example.org", "b@example.org"]


def test_disposition_ext():
    assert google.disposition_ext('attachment; filename="Report 2020.PDF"') == "pdf"
    assert google.disposition_ext("attachment; filename*=UTF-8''r%C3%A9sum%C3%A9.docx") == "docx"
    assert google.disposition_ext("") == ""


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
        google.Google, "pages", lambda _self, _user, url, _key, _params: (responses[url.split("/")[-2]], "")
    )
    g = google.Google()

    assert {key: g.link("user", key) for key in responses} == {
        "unreadable": ("?", False, ""),
        "none": ("", False, ""),
        "my drive": ("reader", False, ""),
        "own": ("writer", False, ""),
        "unknown folder": ("reader", True, ""),
        "inherited": ("reader", True, "F"),
    }
