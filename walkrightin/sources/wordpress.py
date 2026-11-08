import dataclasses
import html
import re
import subprocess

from walkrightin.extract.text import normalize_href
from walkrightin.model import Link
from walkrightin.sources import Source

POST_TYPES = ("post", "page", "nav_menu_item", "wp_navigation", "wp_block")
# Link fields as PHP serializes them (ACF) and as JSON stores them (block attributes).
SERIALIZED_LINK = re.compile(r's:5:"title";s:\d+:"(.*?)";s:3:"url";s:\d+:"(.*?)";', re.DOTALL)
JSON_LINK = re.compile(r'"title":"((?:[^"\\]|\\.)*)","url":"((?:[^"\\]|\\.)*)"')


class WordPressSource(Source):
    """
    The live content of a WordPress site, read from its MySQL database.

    It reads the content and excerpt of published posts of ``post_types``, and their custom fields (meta keys without
    a leading underscore, plus menu item URLs). With WPML, content in an inactive language is skipped. Each link's
    location is ``wp:POST_ID/FIELD``. Mapping a post to its page's URL and title is left to the consumer.
    """

    type = "wordpress"

    def __init__(
        self,
        context,
        *,
        database,
        prefix="wp_",
        post_types=POST_TYPES,
        user="root",
        password="",
        host="",
        port=3306,
        socket="",
        **kwargs,
    ):
        super().__init__(context, **kwargs)
        self.database = database
        self.prefix = prefix
        self.post_types = tuple(post_types)
        self.user = user
        self.password = password
        self.host = host
        self.port = port
        self.socket = socket

    def connect(self):
        import pymysql  # noqa: PLC0415

        options = {"user": self.user, "password": self.password, "database": self.database, "charset": "utf8mb4"}
        if self.host:
            return pymysql.connect(host=self.host, port=self.port, **options)
        socket = (
            self.socket
            or subprocess.run(
                ["mysql", f"-u{self.user}", "-N", "-sse", "SELECT @@socket"],
                capture_output=True,
                text=True,
                check=True,
            ).stdout.strip()
        )
        return pymysql.connect(unix_socket=socket, **options)

    def rows(self):
        """Yield (post ID, field, text) for each piece of live content."""
        p = self.prefix
        placeholders = ",".join(["%s"] * len(self.post_types))
        published = f"p.post_status = 'publish' AND p.post_type IN ({placeholders})"
        with self.connect() as db, db.cursor() as cursor:
            cursor.execute("SHOW TABLES LIKE %s", (f"{p}icl_translations",))
            if cursor.fetchone():
                # WPML doesn't serve content in an inactive language, even if it's published.
                published += f""" AND NOT EXISTS (
                    SELECT 1 FROM {p}icl_translations t JOIN {p}icl_languages l ON l.code = t.language_code
                    WHERE t.element_id = p.ID AND t.element_type = CONCAT('post_', p.post_type) AND l.active = 0)"""  # noqa: S608
            cursor.execute(
                f"SELECT ID, post_content, post_excerpt FROM {p}posts p WHERE {published}",  # noqa: S608
                self.post_types,
            )
            for post_id, content, excerpt in cursor.fetchall():
                if content:
                    yield post_id, "post_content", content
                if excerpt:
                    yield post_id, "post_excerpt", excerpt
            cursor.execute(
                f"""SELECT p.ID, m.meta_key, m.meta_value FROM {p}postmeta m JOIN {p}posts p ON p.ID = m.post_id
                WHERE {published} AND (m.meta_key NOT LIKE '\\_%%' OR m.meta_key = '_menu_item_url')""",  # noqa: S608
                self.post_types,
            )
            yield from (row for row in cursor.fetchall() if row[2])

    def field_links(self, text):
        """Return {URL: title} for the link fields in the text."""
        out = {}
        for pattern in (SERIALIZED_LINK, JSON_LINK):
            for title, value in pattern.findall(text):
                url = normalize_href(value.replace("\\/", "/"), self.extractor.shortlinks)
                if self.extractor.is_link(url):
                    out.setdefault(url, html.unescape(title))
        return out

    def links(self):
        for post_id, field, text in self.rows():
            location = f"wp:{post_id}/{field}"
            found = self.extractor.text(text, "html")
            titles = self.field_links(text)
            for link in found:
                yield dataclasses.replace(link, location=location, text=link.text or titles.pop(link.url, ""))
            for url, title in titles.items():
                if url not in {link.url for link in found}:
                    yield Link(location, url, title)
