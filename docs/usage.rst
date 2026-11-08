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

See :class:`walkrightin.walk.Run` for its other options.

Anonymous and credentials modes
-------------------------------

Without ``[google]`` credentials, walkrightin reads public files through their public export and download URLs, so it reads exactly what the public can. It follows an item if it opens anonymously. It can't list folders, read comments or descriptions, or use the Docs, Sheets, Slides and Forms APIs.

With a service account that has domain-wide delegation, walkrightin reads each item's metadata as the first of ``subjects`` who can see it, and its link role as its shared drive's reader (in ``drive_readers``), or its owner (if in ``domain``), or that subject. A viewer who isn't an owner or organizer sees no permissions. ``items.csv`` records whether the link is the item's own or inherited, and from which folder. It follows an item if it has an anyone-with-link role, or if its role can't be read and it opens anonymously, or if it's in ``also_follow``. Use ``also_follow`` for files you know to be shared with anyone with the link, but whose role the walk can't read and whose anonymous check fails, like a file listed in your Drive's sharing report and covered by the 2021 security update, whose links without a resource key ask visitors to sign in. It reads content as the subject who can see the item. An item that no subject can see is read anonymously, if it opens. The service account needs domain-wide delegation for ``scopes``: by default, Drive's read-only scope, which the Docs, Sheets, Slides and Forms APIs also accept. If its delegation authorizes only the full Drive scope, set ``scopes`` to that.

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
   max_outside_hops = 1               # outside files to read in a row (default: no limit)
   channels = ["export", "api", "comments", "metadata"]
   download_csv = false

   [check]
   items = "all"                      # or "unreadable": check only items whose link role can't be read
   concurrency = 2
   delay = 1.0
   ttl_days = 7
   # user_agent = "..."               # for anonymous requests to Drive

   [short_links]
   extra_domains = ["example.click"]  # an organization's own shortener
   exclude_domains = []
   # domains = [...]                  # replaces the built-in list

   [google]
   credentials = "~/key.json"         # or WALKRIGHTIN_CREDENTIALS
   subjects = ["staff@example.org"]
   domain = "example.org"
   drive_readers = "drives.csv"       # driveid,user: an organizer of each shared drive
   scopes = ["https://www.googleapis.com/auth/drive.readonly"]

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
- **User-Agent.** Every request identifies walkrightin, with a link to this repository. Google answers it as it answers a browser: on 300 Drive URLs, every status was the same. ``user_agent`` overrides it for anonymous requests to Drive.
- **Outside files.** With ``max_outside_hops``, the walk reads at most that many files in a row that your organization doesn't own, counting from a source or one of your files. Your files are those in a shared drive in ``drive_readers``, or owned in ``domain``. The files an outside file links are still recorded, but outside ones beyond the limit aren't read, so that a partner's register of its own files doesn't lead the walk across Drive. ``items.csv`` records each item's ``outside_hops``, and ``run.json`` counts the files left unread. It needs credentials, to know owners.
- **Forms** are read through their public page, which is what respondents see, and with credentials through the Forms API. A form that doesn't accept responses shows no one its questions: its page isn't read, and the API's links are ``hidden``.
- **Published documents.** A published document's URL (``/d/e/...``) has no Drive ID. Unless ``aliases`` maps it to its file, it's an item of its own, whose ``item_id`` is ``e:`` and its published ID. It's checked and read anonymously through its published page. Its owner is unknown, so it counts as an outside file.
- **Colab notebooks** are downloaded, and their cells and text outputs read. A ``colab.research.google.com/drive/ID`` URL is checked as its Drive file, since Colab's page opens for anyone.
- **Types not read,** like images, video and My Maps, are noted in ``scans.csv``, with the channel ``none``. Drawings embedded in Docs aren't read: exports and the Docs API have only their image. **Version history** isn't read: viewers can't open it.

Channels
--------

A Google Doc, Sheet, deck, Drawing or form is read through each of ``channels``. Each link records the channel that found it.

``export``
  The file's export: a Doc as HTML, a Sheet as XLSX (each tab's links, with the tab's visibility), a deck as PPTX, a Drawing as PDF. A raw pass finds every URL in the export. A form, or a published document, is read through its public page.
``api``
  With credentials: the Docs API (every tab, headers, footers, footnotes, smart chips, and links only in suggestions), the Sheets API (cell links, rich text, HYPERLINK formulas, chips, notes) the Slides API (text, shapes, lines, images, tables, groups, speaker notes, layouts and masters) and the Forms API (title, description, questions and options).
``comments``
  With credentials: the links in comments and replies, open and resolved. ``comments.csv`` has each comment, and whether its quoted text is still in a Doc.
``metadata``
  With credentials: the links in the description of a followed item, of any type.

Outputs
-------

``links.csv``
  Each link to Drive: its ``source`` (or ``walk`` for a link in a Drive file), ``location``, ``part`` (body, header, footnote, chip, suggestion, cell, note, comment, resolved_comment, speaker_notes, layout, master, description, output, script, html_comment, attribute:NAME, folder_child, bare_id, ...), ``container`` (a tab, cell, slide or comment), ``visibility`` (visible, hidden, very_hidden, source_only), ``channel``, ``url`` as found, ``text``, ``note``, the anonymous check of a source's URL as found (``url_status``, ``http``), the Drive URL it leads to (``target_url``, and ``short_url`` if through a short link), ``item_id``, ``resource_key`` and ``depth``.
``items.csv``
  Each item reached, by its Drive ID (or ``e:`` and a published document's ID): its anonymous check (``status``, ``http``, ``checked_at``), ``depth``, ``linked_from``, whether it was ``followed``, and with credentials, its ``link_role`` ("" for none, "?" if unreadable), whether that link is inherited rather than its own (``link_inherited``) and from which folder, if Drive says (``link_from``), who could read it (``read_as``) and its metadata.
``scans.csv``
  Each read of an item, by channel: who read it, whether it succeeded, its error, its number of links, and any note. An item of a type that isn't read has a row with the channel ``none``.
``comments.csv``
  Each comment and reply, with credentials.
``run.json``
  The configuration, the times, the links by source, and the counts of rate-limited checks and of files skipped for size.

Statuses: ``public``, ``redirect`` (redirects to a page that opens), ``signin``, ``not_found``, ``gone`` (deleted), ``closed_form``, ``rate_limited`` and ``error``. A source's URL is checked as found, with only the resource key it carries, so a link that visitors can't use without its key shows ``signin``.
