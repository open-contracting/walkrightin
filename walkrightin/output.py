"""Write a run's outputs, each via a temporary file so that no reader sees part of one."""

import csv
import json
from pathlib import Path

LINK_FIELDS = [
    "source",
    "location",
    "part",
    "container",
    "visibility",
    "channel",
    "url",
    "text",
    "note",
    "url_status",
    "http",
    "target_url",
    "short_url",
    "item_id",
    "resource_key",
    "depth",
]
ITEM_FIELDS = [
    "item_id",
    "status",
    "http",
    "checked_at",
    "depth",
    "linked_from",
    "followed",
    "link_role",
    "read_as",
    "name",
    "mime_type",
    "drive_id",
    "owner",
    "modified",
    "size",
    "error",
]
SCAN_FIELDS = ["item_id", "channel", "read_as", "ok", "error", "links", "note"]
COMMENT_FIELDS = [
    "item_id",
    "comment_id",
    "reply_id",
    "author",
    "author_email",
    "me",
    "resolved",
    "created",
    "quote",
    "quote_present",
]


def temporary(path):
    return path.with_name(f"{path.name}.tmp")


def write_csv(path, fieldnames, rows):
    path = Path(path)
    with temporary(path).open("w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)
    temporary(path).replace(path)


def read_csv(path):
    path = Path(path)
    if not path.exists():
        return []
    with path.open(newline="") as f:
        return list(csv.DictReader(f))


def write_json(path, data):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary(path).write_text(json.dumps(data, indent=1, sort_keys=True))
    temporary(path).replace(path)


def read_json(path, default=None):
    path = Path(path)
    if not path.exists():
        return default
    return json.loads(path.read_text())
