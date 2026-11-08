Sources
=======

Built-in sources
----------------

``wordpress``
  A WordPress site's live content, from its MySQL database: the content and excerpt of published posts of ``post_types``, and their custom fields. Content in a language that WPML has deactivated is skipped. Options: ``database``, ``prefix`` (default ``wp_``), ``post_types``, ``user``, ``password``, ``host``, ``port``, ``socket``. Each link's location is ``wp:POST_ID/FIELD``; mapping a post to its page is left to you.
``files``
  The PDF, Word, PowerPoint, Excel and CSV files in a directory, like a website's uploads. Options: ``path``, ``location_prefix``, ``extensions``.
``github``
  The tracked files of public repositories: text files, and PDF, Word, PowerPoint and Excel files. Options: ``orgs``, ``repos``, ``exclude``, ``branches`` ({repository: [branch]}, read as well as the default branch), ``forks``, ``archived``. Each link's location is the file's page on GitHub. Set ``GITHUB_TOKEN`` to raise GitHub's rate limit.
``csv``
  The links in a CSV file. Options: ``path``, and the columns ``url``, ``location`` and ``text``, and a ``separator`` for cells with several values.
``drive``
  Drive files by ID, read and walked whether or not they're public (with credentials, for a private file). Options: ``ids``, ``follow``.

Every source also takes ``name`` and ``bare_ids``.

Write a source
--------------

A source subclasses :class:`walkrightin.sources.Source`, sets ``type``, takes its options as keyword arguments, and yields :class:`walkrightin.model.Link` objects from ``links()``. Use the context's extractor to find links in text or files, so that every source finds links the same way:

.. code-block:: python

   import dataclasses

   from walkrightin.sources import Source


   class IssuesSource(Source):
       """The bodies of a repository's issues."""

       type = "issues"

       def __init__(self, context, *, repo, **kwargs):
           super().__init__(context, **kwargs)
           self.repo = repo

       def links(self):
           url = f"https://api.github.com/repos/{self.repo}/issues"
           for issue in self.context.session.get(url, timeout=30).json():
               for link in self.extractor.text(issue["body"] or "", "markdown"):
                   yield dataclasses.replace(link, location=issue["html_url"])

Register it under the ``walkrightin.sources`` entry-point group, in your package's ``pyproject.toml``:

.. code-block:: toml

   [project.entry-points."walkrightin.sources"]
   issues = "mypackage:IssuesSource"

Then configure it like any other:

.. code-block:: toml

   [[sources]]
   type = "issues"
   repo = "example/project"

A source that knows Drive IDs to read whatever their check can add them to ``self.follow``. A source that skips files for their size can count them in ``self.skipped_for_size``, for ``run.json``.
