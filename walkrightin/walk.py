"""Run the sources, resolve and check their links, and walk the platforms' items they reach."""

import dataclasses
import logging
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path

import requests

from walkrightin import platforms as platforms_module
from walkrightin.check import Checker, now
from walkrightin.extract import Extractor
from walkrightin.model import FOUND, IDENTITY, opens
from walkrightin.output import (
    COMMENT_FIELDS,
    ITEM_FIELDS,
    LINK_FIELDS,
    SCAN_FIELDS,
    read_csv,
    read_json,
    write_csv,
    write_json,
)
from walkrightin.shortlinks import ShortLinks
from walkrightin.sources import Context, create
from walkrightin.urls import WRAPPERS

logger = logging.getLogger(__name__)

WALK = "walk"
ANONYMOUS = "anonymous"


@dataclass
class Result:
    links: int = 0
    items: int = 0
    rate_limited: int = 0
    skipped_for_size: dict = field(default_factory=dict)
    skipped_for_outside_hops: int = 0


class Run:
    """
    One run into ``out``, a directory.

    With ``only`` (source names) and an existing run in ``out``, only those sources run again; the others' links are
    kept. With ``resume``, an interrupted walk continues from its last complete level. Without ``walk``, only the
    sources run, and their links are checked.
    """

    def __init__(self, config, out, *, only=None, resume=False, walk=True):
        self.config = config
        self.out = out
        self.only = set(only or ())
        self.resume = resume
        self.walk_enabled = walk
        self.out = Path(out)
        self.out.mkdir(parents=True, exist_ok=True)
        cache = Path(config.cache_dir)
        cache.mkdir(parents=True, exist_ok=True)
        self.session = requests.Session()
        self.session.headers["User-Agent"] = IDENTITY
        self.downloads_cache_path = cache / "downloads.json"
        self.downloads_cache = read_json(self.downloads_cache_path, {})
        context = platforms_module.Context(
            walk=config.walk,
            folder_limit=config.folder_limit,
            user_agent=config.check.user_agent,
            downloads=self.downloads_cache,
        )
        self.platforms = platforms_module.load(config.platforms, context)
        self.shortlinks = ShortLinks(
            config.short_links.domains,
            config.short_links.extra_domains,
            config.short_links.exclude_domains,
            session=self.session,
            platforms=self.platforms,
        )
        self.shortlinks_cache = cache / "shortlinks.json"
        self.shortlinks.load(self.shortlinks_cache)
        self.wrappers = [*WRAPPERS, *((w[0], w[1], tuple(w[2])) for w in config.wrappers)]
        self.extractor = Extractor(self.shortlinks, wrappers=self.wrappers)
        context.extractor = self.extractor
        self.checker = Checker(
            cache / "checks.json",
            ttl_days=config.check.ttl_days,
            concurrency=config.check.concurrency,
            delay=config.check.delay,
            session=self.session,
            user_agent=config.check.user_agent,
            platforms=self.platforms,
        )
        self.channels = set(config.walk.channels)
        self.also_follow = self.read_ids(config.walk.also_follow)
        self.result = Result()
        self.path = lambda name: self.out / name

    # Inputs

    @staticmethod
    def read_ids(path):
        if not path:
            return set()
        lines = Path(path).read_text().splitlines()
        return {line.strip() for line in lines if line.strip() and not line.startswith("#")}

    # Links

    def target(self, url):
        """Return (platform URL or None, short URL or "") for a link's URL."""
        if self.platforms.match(url):
            return url, ""
        if self.shortlinks.is_short(url):
            return self.shortlinks.resolve(url), url
        return None, ""

    def resolve(self, links):
        """Resolve short links in parallel, so that target() finds them cached."""
        shorts = sorted(
            {
                link.url
                for _, link, _ in links
                if not self.platforms.match(link.url) and self.shortlinks.is_short(link.url)
            }
        )
        with ThreadPoolExecutor(8) as pool:
            list(pool.map(self.shortlinks.follow, shorts))
        self.shortlinks.save(self.shortlinks_cache)

    def row(self, source, link, depth):
        """Return the links.csv row for a link, or None if it leads to no platform and has no note to check it."""
        target, short = self.target(link.url)
        if not target and not link.note:
            return None
        key, resource_key = self.platforms.key(target) if target else ("", "")
        return {
            **dataclasses.asdict(link),
            "source": source,
            "target_url": target or "",
            "short_url": short,
            "item_id": key,
            "resource_key": resource_key,
            "url_status": "",
            "http": "",
            "depth": depth,
        }

    # Sources

    def previous_links(self):
        """Return the existing run's links.csv rows. Fail if a key isn't an installed platform's."""
        rows = read_csv(self.path("links.csv"))
        names = {p.name for p in self.platforms}
        for r in rows:
            if r["item_id"] and platforms_module.split_key(r["item_id"])[0] not in names:
                msg = f"{self.path('links.csv')}: {r['item_id']} isn't an installed platform's key. Run in full again."
                raise ValueError(msg)
        return rows

    def source_links(self):
        """Return the links.csv rows of the sources' links, checked, with each source's count."""
        context = Context(
            extractor=self.extractor,
            cache_dir=self.config.cache_dir,
            max_text_bytes=self.config.walk.max_text_kb * 1000,
            session=self.session,
            platforms=self.platforms,
        )
        kept = []
        if self.only:
            kept = [r for r in self.previous_links() if r["source"] not in self.only and r["source"] != WALK]
        found = []
        for options in self.config.sources:
            source = create(context, options)
            self.also_follow |= source.follow
            if self.only and source.name not in self.only:
                continue
            logger.info("reading source %s", source.name)
            found += [(source.name, link, 0) for link in source.links()]
            if n := getattr(source, "skipped_for_size", 0):
                self.result.skipped_for_size[source.name] = n
        self.resolve(found)
        rows = [r for args in found if (r := self.row(*args))]
        # Each URL is checked as found, with only the resource key it carries.
        results = self.checker.check_all(r["target_url"] for r in rows if r["target_url"])
        for r in rows:
            if r["target_url"]:
                r["url_status"] = results[r["target_url"]]["status"]
                r["http"] = results[r["target_url"]]["http"]
        linked = {(r["source"], r["item_id"]) for r in rows + kept if r["part"] != "bare_id"}
        # Most strings that look like IDs aren't, so their platform must find them.
        # One that its source links by URL is redundant.
        rows = [
            r
            for r in rows
            if r["part"] != "bare_id" or (r["url_status"] in FOUND and (r["source"], r["item_id"]) not in linked)
        ]
        self.result.rate_limited += sum(1 for r in rows if r["url_status"] == "rate_limited")
        return kept + rows

    # Items

    def inspect(self, key, resource_key, url):
        """Return what's known of an item: its metadata, link role, anonymous check, and who can read it."""
        platform, item_id = self.platforms.item(key)
        m = {**platform.describe(item_id, resource_key, url), "item_id": key}
        if not platform.credentialed or self.config.check.items == "all" or m["link_role"] == "?":
            result = self.checker.check(platform.item_check_url(item_id, m))
            m.update(status=result["status"], http=result["http"], checked_at=result["checked_at"])
            platform.after_check(m, result)
        return m

    def own(self, key, m):
        return self.platforms.item(key)[0].owns(m)

    def within_outside_hops(self, key, m):
        limit = self.config.walk.max_outside_hops
        # Without credentials, owners are unknown.
        return limit is None or not self.platforms.item(key)[0].credentialed or int(m["outside_hops"]) <= limit

    def followed(self, key, m):
        if key in self.also_follow:
            return True
        if m["link_role"] not in ("", "?"):
            return True
        return m["link_role"] == "?" and opens(m.get("status", ""))

    # Scans

    def scan(self, key, m, depth):
        """Return (links.csv rows, scans.csv rows, comments.csv rows) for a followed item."""
        platform, item_id = self.platforms.item(key)
        user = m["read_as"]
        # An item that the platform's credentials can't open is read anonymously if it opens.
        if not user and platform.credentialed and not opens(m.get("status", "")):
            return [], [], []
        location = platform.location(item_id, m)
        results = []
        if user and m.get("description") and "metadata" in self.channels:
            results.append(
                platforms_module.Scan("metadata", self.extractor.text(m["description"], part="description"))
            )
        results += platform.read(item_id, m)
        links, scans, comments = [], [], []
        for result in results:
            found = [dataclasses.replace(link, location=location, channel=result.channel) for link in result.links]
            scans.append(
                {
                    "item_id": key,
                    "channel": result.channel,
                    "read_as": result.read_as or user or ANONYMOUS,
                    "ok": "1" if result.read and not result.error else "",
                    "error": result.error,
                    "links": len(found),
                    "note": result.note,
                }
            )
            links.extend(found)
            comments.extend({**row, "item_id": key} for row in result.comments)
        return [r for link in links if (r := self.row(WALK, link, depth))], scans, comments

    # The walk

    def walk(self, rows, state):
        """Walk from the items that the rows link to, level by level, writing the outputs after each level."""
        seen = state.get("seen") or {}
        keys = state.get("keys") or {}
        info = state.get("info") or {}
        urls = state.get("urls") or {}
        # The fewest outside files in a row on the way to each item, through its parents at the shortest depth.
        runs = state.get("runs") or {}
        level, depth = state.get("level"), state.get("depth", 0)
        if level is None:
            for r in rows:
                if r["item_id"]:
                    seen.setdefault(r["item_id"], [0, r["location"]])
                    urls.setdefault(r["item_id"], r["target_url"])
                    if r["resource_key"]:
                        keys.setdefault(r["item_id"], r["resource_key"])
            level = sorted(seen)
        walk_rows = [r for r in rows if r["source"] == WALK]
        rows = [r for r in rows if r["source"] != WALK]
        scans = read_csv(self.path("scans.csv")) if self.resume else []
        comments = read_csv(self.path("comments.csv")) if self.resume else []
        max_depth = self.config.walk.max_depth
        while level and depth <= max_depth:
            with ThreadPoolExecutor(self.config.walk.workers) as pool:
                metas = list(pool.map(lambda i: self.inspect(i, keys.get(i, ""), urls.get(i, "")), level))
            to_scan = []
            for i, m in zip(level, metas, strict=True):
                m["followed"] = "1" if self.followed(i, m) else ""
                m["outside_hops"] = 0 if self.own(i, m) else runs.get(i, 0) + 1
                info[i] = m
                if m["followed"] and depth < max_depth:
                    if self.within_outside_hops(i, m):
                        to_scan.append(i)
                    else:
                        self.result.skipped_for_outside_hops += 1
            if depth == max_depth and (unfollowed := [i for i in level if info[i]["followed"]]):
                logger.warning("depth limit %d reached: %d followed items not read", max_depth, len(unfollowed))
            with ThreadPoolExecutor(self.config.walk.workers) as pool:
                results = list(pool.map(lambda i, d=depth: self.scan(i, info[i], d), to_scan))
            nxt = []
            for i, (found, scan_rows, comment_rows) in zip(to_scan, results, strict=True):
                walk_rows += found
                scans = [s for s in scans if s["item_id"] != i] + scan_rows
                comments = [c for c in comments if c["item_id"] != i] + comment_rows
                for r in found:
                    j = r["item_id"]
                    if j and r["resource_key"]:
                        keys.setdefault(j, r["resource_key"])
                    if j and j != i and j not in seen:
                        seen[j] = [depth + 1, i]
                        urls[j] = r["target_url"]
                        runs[j] = info[i]["outside_hops"]
                        nxt.append(j)
                    elif j and seen.get(j, [None])[0] == depth + 1:
                        runs[j] = min(runs.get(j, 0), info[i]["outside_hops"])
            level, depth = nxt, depth + 1
            state = {
                "seen": seen,
                "keys": keys,
                "info": info,
                "urls": urls,
                "runs": runs,
                "level": level,
                "depth": depth,
            }
            self.write(rows + walk_rows, seen, info, scans, comments, state)
            logger.info("depth %d: %d items, %d read, %d new", depth - 1, len(metas), len(to_scan), len(nxt))
        return rows + walk_rows, seen, info, scans, comments

    def write(self, rows, seen, info, scans, comments, state):
        items = [
            {**info[i], "item_id": i, "depth": d, "linked_from": src}
            for i, (d, src) in sorted(seen.items(), key=lambda kv: (kv[1][0], kv[0]))
            if i in info
        ]
        write_csv(self.path("links.csv"), LINK_FIELDS, rows)
        write_csv(self.path("items.csv"), ITEM_FIELDS, items)
        write_csv(self.path("scans.csv"), SCAN_FIELDS, scans)
        write_csv(self.path("comments.csv"), COMMENT_FIELDS, comments)
        write_json(self.path("state.json"), state)
        write_json(self.downloads_cache_path, self.downloads_cache)
        self.checker.save()
        self.shortlinks.save(self.shortlinks_cache)

    def execute(self):
        started = now()
        state = read_json(self.path("state.json"), {}) if self.resume else {}
        if self.resume and state:
            rows = self.previous_links()
        else:
            rows = self.source_links()
            state = {}
        if self.walk_enabled:
            rows, seen, info, scans, comments = self.walk(rows, state)
        else:
            seen, info, scans, comments = {}, {}, [], []
        self.write(rows, seen, info, scans, comments, {})
        skipped = sum(1 for s in scans if s.get("note", "").startswith("skipped: larger than"))
        if skipped:
            self.result.skipped_for_size[WALK] = skipped
        self.result.rate_limited += sum(1 for m in info.values() if m.get("status") == "rate_limited")
        self.result.links = len(rows)
        self.result.items = len(seen)
        counts = {}
        for r in rows:
            counts[r["source"]] = counts.get(r["source"], 0) + 1
        previous = read_json(self.path("run.json"), {})
        names = [s.get("name") or s["type"] for s in self.config.sources]
        write_json(
            self.path("run.json"),
            {
                "started": previous.get("started", started) if self.only else started,
                "finished": now(),
                "sources": names,
                "complete": True,
                "links": counts,
                "items": len(seen),
                "rate_limited": self.result.rate_limited,
                "skipped_for_size": self.result.skipped_for_size,
                "skipped_for_outside_hops": self.result.skipped_for_outside_hops,
                "config": dataclasses.asdict(self.config),
                "refreshed": sorted(self.only),
            },
        )
        self.path("state.json").unlink(missing_ok=True)
        return self.result


def run(config, out, **kwargs):
    """Run the sources and walk from their links into ``out``. Return a Result."""
    return Run(config, out, **kwargs).execute()
