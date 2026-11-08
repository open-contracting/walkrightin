Platforms
=========

A source is a place that's configured and listed in full, like a WordPress database. A platform is a place that links point to, reached only by following a link: walkrightin recognizes its URLs, identifies the item, checks what an anonymous visitor sees, and reads the item to follow its links further. Each platform's options are in its ``[platforms.NAME]`` table, and its items' keys start with its name.

Built-in platforms
------------------

``google_drive``
  Google Drive: files, folders, Docs, Sheets, Slides, Drawings, forms, Colab notebooks and published documents.

  .. code-block:: toml

     [platforms.google_drive]
     credentials = "~/key.json"         # or WALKRIGHTIN_GOOGLE_DRIVE_CREDENTIALS
     subjects = ["staff@example.org"]
     domain = "example.org"
     drive_readers = "drives.csv"       # driveid,user: an organizer of each shared drive
     aliases = "form_aliases.csv"       # published_id,file_id: maps published URLs (/d/e/...) to files
     scopes = ["https://www.googleapis.com/auth/drive.readonly"]

Google Drive
------------

Without credentials, walkrightin reads public files through their public export and download URLs. It can't list folders, read comments or descriptions, or use the Docs, Sheets, Slides and Forms APIs.

With a service account that has domain-wide delegation, walkrightin reads each item's metadata as the first of ``subjects`` who can see it, and its link role as its shared drive's reader (in ``drive_readers``), or its owner (if in ``domain``), or that subject. A viewer who isn't an owner or organizer sees no permissions. ``items.csv`` records whether the link is the item's own or inherited, and from which folder. It reads content as the subject who can see the item. The service account needs domain-wide delegation for ``scopes``: by default, Drive's read-only scope, which the Docs, Sheets, Slides and Forms APIs also accept. If its delegation authorizes only the full Drive scope, set ``scopes`` to that.

Use ``also_follow`` for files you know to be shared with anyone with the link, but whose role the walk can't read and whose anonymous check fails, like a file listed in your Drive's sharing report and covered by the 2021 security update, whose links without a resource key ask visitors to sign in.

Choices to know about:

- **Your files** are those in a shared drive in ``drive_readers`` (an item's ``space`` is its shared drive), or owned in ``domain``.
- **Rate limits.** Google answers bursts of requests with a captcha for a while: the check is ``rate_limited``.
- **User-Agent.** Google answers walkrightin's as it answers a browser: on 300 Drive URLs, every status was the same.
- **Forms** are read through their public page, which is what respondents see, and with credentials through the Forms API. A form that doesn't accept responses shows no one its questions: its page isn't read, and the API's links are ``hidden``.
- **Published documents.** A published document's URL (``/d/e/...``) has no Drive ID. Unless ``aliases`` maps it to its file, it's an item of its own, whose key is ``google_drive:e:`` and its published ID. It's checked and read anonymously through its published page. Its owner is unknown, so it counts as an outside file.
- **Colab notebooks** are downloaded, and their cells and text outputs read. A ``colab.research.google.com/drive/ID`` URL is checked as its Drive file, since Colab's page opens for anyone.
- **Types not read** include images, video, My Maps and Apple iWork files. Drawings embedded in Docs aren't read: exports and the Docs API have only their image. **Version history** isn't read: viewers can't open it.

Its channels:

``export``
  A Doc as HTML, a Sheet as XLSX (each tab's links, with the tab's visibility), a deck as PPTX, a Drawing as PDF. A form, or a published document, is read through its public page.
``api``
  The Docs API (every tab, headers, footers, footnotes, smart chips, and links only in suggestions), the Sheets API (cell links, rich text, HYPERLINK formulas, chips, notes), the Slides API (text, shapes, lines, images, tables, groups, speaker notes, layouts and masters) and the Forms API (title, description, questions and options).
``comments``
  A Doc's, Sheet's or deck's comments. ``comments.csv`` records whether a comment's quoted text is still in a Doc.

Write a platform
----------------

A platform subclasses :class:`walkrightin.platforms.Platform`, sets ``name`` and ``pattern`` (the start of its URLs), and implements:

- ``key(url)``: the item's ID and resource key. An ID can be a handle that only the owner can map to the item, like a published document's or a shared view's.
- ``item_check_url(item_id, meta)``: the URL to check the item anonymously.
- ``location(item_id, meta)``: the URL to name the item by, as the location of the links in it.
- ``read(item_id, meta)``: a :class:`walkrightin.platforms.Scan` per channel. ``listing()`` lists a container's children, up to ``max_folder_children``.

With credentials, it overrides ``credentialed``, ``describe()`` (the item's metadata and link role) and ``owns()``. It can also override ``identifies()``, ``url()``, ``bare_ids()``, ``continues()``, ``check_url()``, ``redirect_status()`` and ``after_check()``. Its ``options`` dataclass is its ``[platforms.NAME]`` table, with ``paths`` resolved relative to the configuration file and ``environ`` overriding options.

Register it under the ``walkrightin.platforms`` entry-point group, in your package's ``pyproject.toml``:

.. code-block:: toml

   [project.entry-points."walkrightin.platforms"]
   box = "mypackage:Box"
