Usage
=====

Install
-------

.. code-block:: bash

   pip install walkrightin               # anonymous mode
   pip install walkrightin[google-drive] # with a Google service account
   pip install walkrightin[wordpress]    # to read a WordPress database

To read PDFs, install `qpdf <https://qpdf.readthedocs.io>`__ and Poppler's ``pdftotext`` (``brew install qpdf poppler`` or ``apt install qpdf poppler-utils``). Without them, each PDF is recorded with an error. The ``github`` source needs ``git``.

Run
---

.. code-block:: bash

   walkrightin run -c walkrightin.toml -o runs/2026-11-07

A run reads every source, follows short links to platforms, checks each platform URL anonymously, then walks the items that the links reach, up to ``max_depth`` levels. It writes its outputs after each level. If it's interrupted, continue it with ``--resume``.

- ``--no-walk`` only reads the sources and checks their links.
- ``--source NAME`` reads one source again in an existing run, keeps the other sources' links, and walks again. The run stays complete, so its other sources' links stay current.
- ``--depth N`` overrides ``max_depth``.

A platform can answer bursts of requests with a captcha for a while. A check that's rate-limited isn't cached, and ``run`` exits non-zero; run it again later. Checks are cached for ``ttl_days``, and followed short links indefinitely, in the cache directory (default ``~/.cache/walkrightin``).

Other commands:

``walkrightin check URL...``
  Check how an anonymous visitor sees each URL.
``walkrightin extract FILE...``
  Print the links to platforms in each file.
``walkrightin sources``
  List the installed source types.
``walkrightin platforms``
  List the installed platforms.

From Python:

.. code-block:: python

   import walkrightin
   from walkrightin.config import load

   result = walkrightin.run(load("walkrightin.toml"), "runs/2026-11-07")

See :class:`walkrightin.walk.Run` for its other options.

Anonymous and credentials modes
-------------------------------

Without a platform's credentials, walkrightin reads its public items through their public URLs, so it reads exactly what the public can. It follows an item if it opens anonymously.

With credentials, walkrightin reads each item's metadata, and the role of anyone with its link. It follows an item if it has an anyone-with-link role, or if its role can't be read and it opens anonymously, or if its key is in ``also_follow``. An item that the credentials can't read is read anonymously, if it opens. See :doc:`platforms` for what each platform's credentials are and read.

Configure
---------

.. code-block:: toml

   cache_dir = "~/.cache/walkrightin"

   [walk]
   max_depth = 8
   also_follow = "keys.txt"           # item keys to follow whatever their check, one per line
   max_download_mb = 200
   max_text_kb = 1000                 # larger text files in repositories are data, not pages
   max_folder_children = 1000         # 0 doesn't walk folders; "all" walks every folder
   max_outside_hops = 1               # outside files to read in a row (default: no limit)
   channels = ["export", "api", "comments", "metadata"]
   download_csv = false

   [check]
   items = "all"                      # or "unreadable": check only items whose link role can't be read
   concurrency = 2
   delay = 1.0
   ttl_days = 7
   # user_agent = "..."               # for anonymous requests to platforms

   [short_links]
   extra_domains = ["example.click"]  # an organization's own shortener
   exclude_domains = []
   # domains = [...]                  # replaces the built-in list

   [platforms.google_drive]
   credentials = "~/key.json"         # or WALKRIGHTIN_GOOGLE_DRIVE_CREDENTIALS
   subjects = ["staff@example.org"]

   [[sources]]
   type = "github"
   orgs = ["example"]
   branches = {"example/docs" = ["1.1-dev"]}
   bare_ids = true

Relative paths are relative to the configuration file. Give each source of the same type a distinct ``name``. Each platform's options are in its ``[platforms.NAME]`` table: see :doc:`platforms`.

Choices to know about:

- **Item keys.** An item's key is its platform's name and the platform's ID, like ``google_drive:1AbC...``. ``items.csv``, ``links.csv``, ``scans.csv``, ``comments.csv`` and ``also_follow`` use keys.
- **Short links.** The shortener domains are `antispam-link-shorteners <https://pypi.org/project/antispam-link-shorteners/>`__' list, without single-site shorteners like youtu.be that can't lead to a platform. A short link is kept only if it leads to a platform.
- **Bare IDs.** With ``bare_ids`` on a source, strings that look like a platform's ID outside a URL (like a notebook's Sheet ID) are recorded as the platform's URLs, and kept only if the platform finds an item.
- **URLs without an identifier,** like a template (``/document/d/$``) or ``drive.google.com/drive/my-drive``, aren't links to an item, and are skipped.
- **CSV files** on a platform aren't downloaded unless ``download_csv`` is set, because their URLs are usually data rather than links. A ``files`` source reads CSV files by default, in ``extensions``.
- **Size limits.** Files over ``max_download_mb`` (on a platform) or ``max_text_kb`` (text files in repositories) aren't read. ``run.json`` counts them, by source, and ``scans.csv`` notes each skipped file.
- **User-Agent.** Every request identifies walkrightin, with a link to this repository. ``user_agent`` overrides it for anonymous requests to platforms.
- **Outside files.** With ``max_outside_hops``, the walk reads at most that many items in a row that your organization doesn't own, counting from a source or one of your items. The items an outside item links are still recorded, but outside ones beyond the limit aren't read, so that a partner's register of its own files doesn't lead the walk across a platform. ``items.csv`` records each item's ``outside_hops``, and ``run.json`` counts the items left unread. It needs credentials, to know owners.
- **Types not read,** like images and video, are noted in ``scans.csv``, with the channel ``none``.

Channels
--------

An item is read through each of ``channels`` that its platform offers. Each link records the channel that found it.

``export``
  The item's content, as an anonymous visitor or the credentials can export it. A raw pass finds every URL in it.
``api``
  With credentials: the platform's API for the item's content.
``comments``
  With credentials: the links in comments and replies, open and resolved. ``comments.csv`` has each comment.
``metadata``
  With credentials: the links in the description of a followed item, of any type.

Platforms also read items through channels that aren't configured: ``download`` (a file), ``listing`` (a folder's children, up to ``max_folder_children``) and ``none`` (a type that isn't read).

Outputs
-------

``links.csv``
  Each link to a platform: its ``source`` (or ``walk`` for a link in an item), ``location``, ``part`` (body, header, footnote, chip, suggestion, cell, note, comment, resolved_comment, speaker_notes, layout, master, description, output, script, html_comment, attribute:NAME, folder_child, bare_id, ...), ``container`` (a tab, cell, slide or comment), ``visibility`` (visible, hidden, very_hidden, source_only, or blank for a link whose text is empty or only whitespace, so that no reader sees it), ``channel``, ``url`` as found, ``text``, ``note``, the anonymous check of a source's URL as found (``url_status``, ``http``), the platform URL it leads to (``target_url``, and ``short_url`` if through a short link), its item's key (``item_id``), the ``resource_key`` that the URL carries and ``depth``.
``items.csv``
  Each item reached, by its key: its anonymous check (``status``, ``http``, ``checked_at``), ``depth``, ``linked_from`` (an item's key, or a source's location), whether it was ``followed``, and with credentials, its ``link_role`` ("" for none, "?" if unreadable), whether that link is inherited rather than its own (``link_inherited``) and from which folder, if the platform says (``link_from``), who could read it (``read_as``) and its metadata: ``name``, ``mime_type``, the ``space`` whose owner decides whether your organization owns it (like a shared drive), ``owner``, ``modified`` and ``size``.
``scans.csv``
  Each read of an item, by channel: who read it, whether it succeeded, its error, its number of links, and any note. An item of a type that isn't read has a row with the channel ``none``.
``comments.csv``
  Each comment and reply, with credentials.
``run.json``
  The configuration, the times, the links by source, and the counts of rate-limited checks and of files skipped for size.

Statuses: ``public``, ``redirect`` (redirects to a page that opens), ``signin``, ``not_found``, ``gone`` (deleted), ``closed_form``, ``rate_limited`` and ``error``. A source's URL is checked as found, with only the resource key it carries, so a link that visitors can't use without its key shows ``signin``.
