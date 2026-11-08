|PyPI Version| |Build Status| |Coverage Status| |Python Version|

Audit the websites, uploads and repositories that your organization publishes, to find the Google Drive files they link to, directly or through other Drive files, and check whether each opens to anyone with the link. walkrightin requests only public URLs, plus whatever your own service account can read.

Sources are plugins: a WordPress site's database, a directory of files (like a website's uploads), GitHub organizations' repositories, a CSV of links, and Drive files by ID. Each yields where a link was found, its URL and its text. walkrightin follows short links (bit.ly, forms.gle, etc.), checks each Drive URL as an anonymous visitor would, then walks the Docs, Sheets, Slides, Drawings, forms, published documents, PDF and Office files, Colab notebooks, and folders, that the links reach.

.. code-block:: bash

   pip install walkrightin[google,wordpress]
   walkrightin run -c walkrightin.toml -o runs/2026-11-07

(If you are viewing this on GitHub, open the `full documentation <https://walkrightin.readthedocs.io/>`__ for additional details.)

.. |PyPI Version| image:: https://img.shields.io/pypi/v/walkrightin.svg
   :target: https://pypi.org/project/walkrightin/
.. |Build Status| image:: https://github.com/open-contracting/walkrightin/actions/workflows/ci.yml/badge.svg
   :target: https://github.com/open-contracting/walkrightin/actions/workflows/ci.yml
.. |Coverage Status| image:: https://codecov.io/github/open-contracting/walkrightin/graph/badge.svg
   :target: https://codecov.io/github/open-contracting/walkrightin
.. |Python Version| image:: https://img.shields.io/pypi/pyversions/walkrightin.svg
   :target: https://pypi.org/project/walkrightin/
