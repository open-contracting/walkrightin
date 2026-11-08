Usage
=====

Install
-------

.. code-block:: bash

   pip install walkrightin            # anonymous mode
   pip install walkrightin[google]    # with a service account
   pip install walkrightin[wordpress] # to read a WordPress database

To read PDFs, install `qpdf <https://qpdf.readthedocs.io>`__ and Poppler's ``pdftotext`` (``brew install qpdf poppler`` or ``apt install qpdf poppler-utils``). Without them, each PDF is recorded with an error. The ``github`` source needs ``git``.

Run
---

.. code-block:: bash

   walkrightin run -c walkrightin.toml -o runs/2026-11-07

A run reads every source, follows short links to Drive, checks each Drive URL anonymously, then walks the Drive files that the links reach, up to ``max_depth`` levels. It writes its outputs after each level. If it's interrupted, continue it with ``--resume``.

- ``--no-walk`` only reads the sources and checks their links.
- ``--source NAME`` reads one source again in an existing run, keeps the other sources' links, and walks again. The run stays complete, so its other sources' links stay current.
- ``--depth N`` overrides ``max_depth``.

Google answers bursts of requests with a captcha for a while. A check that's rate-limited isn't cached, and ``run`` exits non-zero; run it again later. Checks are cached for ``ttl_days``, and followed short links indefinitely, in the cache directory (default ``~/.cache/walkrightin``).

Other commands:

``walkrightin check URL...``
  Check how an anonymous visitor sees each URL.
``walkrightin extract FILE...``
  Print the links to Drive in each file.
``walkrightin sources``
  List the installed source types.

From Python:

.. code-block:: python

   import walkrightin
   from walkrightin.config import load

   result = walkrightin.run(load("walkrightin.toml"), "runs/2026-11-07")

``run()`` also accepts ``reader`` and ``permissions`` functions, to choose who reads an item's permissions and how its link role is read. See :class:`walkrightin.walk.Run`.

Anonymous and credentials modes
-------------------------------

Without ``[google]`` credentials, walkrightin reads public files through their public export and download URLs, so it reads exactly what the public can. It follows an item if it opens anonymously. It can't list folders, read comments, or use the Docs, Sheets and Slides APIs.

With a service account that has domain-wide delegation, walkrightin reads each item's metadata as the first of ``subjects`` who can see it, and its link role as its owner (if in ``domain``) or that subject. It follows an item if it has an anyone-with-link role, or if its role can't be read and it opens anonymously, or if it's in ``also_follow``. It reads content as the subject who can see the item. An item that no subject can see is read anonymously, if it opens. Grant the service account the read-only scopes for Drive, Docs, Sheets and Slides.

Configure
---------

.. code-block:: toml

   cache_dir = "~/.cache/walkrightin"

   [walk]
   max_depth = 8
   also_follow = "ids.txt"            # Drive IDs to follow whatever their check, one per line
   aliases = "form_aliases.csv"       # published_id,file_id: maps published URLs (/d/e/...) to files
   max_download_mb = 200
   max_text_kb = 1000                 # larger text files in repositories are data, not pages
   max_folder_children = 1000         # 0 doesn't walk folders; "all" walks every folder
   channels = ["export", "api", "comments"]
   download_csv = false

   [check]
   items = "all"                      # or "unreadable": check only items whose link role can't be read
   concurrency = 2
   delay = 1.0
   ttl_days = 7

   [short_links]
   extra_domains = ["example.click"]  # an organization's own shortener
   exclude_domains = []
   # domains = [...]                  # replaces the built-in list

   [google]
   credentials = "~/key.json"         # or WALKRIGHTIN_CREDENTIALS
   subjects = ["staff@example.org"]
   domain = "example.org"

   [[sources]]
   type = "github"
   orgs = ["example"]
   branches = {"example/docs" = ["1.1-dev"]}
   bare_ids = true

Relative paths are relative to the configuration file. Give each source of the same type a distinct ``name``.

Choices to know about:

- **Short links.** The shortener domains are `antispam-link-shorteners <https://pypi.org/project/antispam-link-shorteners/>`__' list, without single-site shorteners like youtu.be that can't lead to Drive. A short link is kept only if it leads to Drive.
- **Bare IDs.** With ``bare_ids`` on a source, strings that look like a file's ID outside a URL (like a notebook's Sheet ID) are recorded as file URLs, and kept only if Drive finds a file.
- **URLs without an identifier,** like a template (``/document/d/$``) or ``drive.google.com/drive/my-drive``, aren't links to an item, and are skipped.
- **CSV files in Drive** aren't downloaded unless ``download_csv`` is set, because their URLs are usually data rather than links. A ``files`` source reads CSV files by default, in ``extensions``.
- **Size limits.** Files over ``max_download_mb`` (in Drive) or ``max_text_kb`` (text files in repositories) aren't read. ``run.json`` counts them, by source, and ``scans.csv`` notes each skipped Drive file.
- **Forms** are checked but not read. **Version history** isn't read: viewers can't open it.

Channels
--------

A Google Doc, Sheet or deck is read through each of ``channels``. Each link records the channel that found it.

``export``
  The file's export: a Doc as HTML, a Sheet as XLSX (each tab's links, with the tab's visibility), a deck as PPTX. A raw pass finds every URL in the export.
``api``
  With credentials: the Docs API (every tab, headers, footers, footnotes, smart chips, and links only in suggestions), the Sheets API (cell links, rich text, HYPERLINK formulas, chips, notes) and the Slides API (text, shapes, lines, images, tables, groups, speaker notes).
``comments``
  With credentials: the links in comments and replies, open and resolved. ``comments.csv`` has each comment, and whether its quoted text is still in a Doc.

Outputs
-------

``links.csv``
  Each link to Drive: its ``source`` (or ``walk`` for a link in a Drive file), ``location``, ``part`` (body, header, footnote, chip, suggestion, cell, note, comment, resolved_comment, speaker_notes, script, html_comment, attribute:NAME, folder_child, bare_id, ...), ``container`` (a tab, cell, slide or comment), ``visibility`` (visible, hidden, very_hidden, source_only), ``channel``, ``url`` as found, ``text``, ``note``, the anonymous check of a source's URL as found (``url_status``, ``http``), the Drive URL it leads to (``target_url``, and ``short_url`` if through a short link), ``item_id``, ``resource_key`` and ``depth``.
``items.csv``
  Each item reached: its anonymous check (``status``, ``http``, ``checked_at``), ``depth``, ``linked_from``, whether it was ``followed``, and with credentials, its ``link_role`` ("" for none, "?" if unreadable), who could read it (``read_as``) and its metadata.
``scans.csv``
  Each read of an item, by channel: who read it, whether it succeeded, its error, its number of links, and any note.
``comments.csv``
  Each comment and reply, with credentials.
``run.json``
  The configuration, the times, the links by source, and the counts of rate-limited checks and of files skipped for size.

Statuses: ``public``, ``redirect`` (redirects to a page that opens), ``signin``, ``not_found``, ``gone`` (deleted), ``closed_form``, ``rate_limited`` and ``error``. A source's URL is checked as found, with only the resource key it carries, so a link that visitors can't use without its key shows ``signin``.
