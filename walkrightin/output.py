"""Write a run's outputs, each via a temporary file so that no reader sees part of one."""

import csv
import json
import os

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


def write_csv(path, fieldnames, rows):
    with open(path + ".tmp", "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)
    os.replace(path + ".tmp", path)


def read_csv(path):
    if not os.path.exists(path):
        return []
    with open(path, newline="") as f:
        return list(csv.DictReader(f))


def write_json(path, data):
    with open(path + ".tmp", "w") as f:
        json.dump(data, f, indent=1, sort_keys=True)
    os.replace(path + ".tmp", path)


def read_json(path, default=None):
    if not os.path.exists(path):
        return default
    with open(path) as f:
        return json.load(f)
