import dataclasses
import logging
import os
import re
import subprocess

from walkrightin.extract import DOCUMENTS, kind_of
from walkrightin.sources import Source

logger = logging.getLogger(__name__)

PER_PAGE = 100
# A page on GitHub, as a location, so that a consumer can find its repository, branch and path.
PAGE = re.compile(r"https://github\.com/([^/]+/[^/]+)/blob/([^/]+)/(.+)")


class GitHubSource(Source):
    """
    The tracked files of public GitHub repositories: text files, and PDF, Word, PowerPoint and Excel files.

    ``orgs`` lists organizations, whose public repositories are read, including forks and archived ones. ``repos``
    lists other repositories. Each repository's default branch is read, plus any in ``branches`` ({repo: [branch]}).
    Each link's location is the file's page on GitHub. Clones are shallow, in the cache directory. Set GITHUB_TOKEN
    to raise GitHub's rate limit.
    """

    type = "github"

    def __init__(self, context, *, orgs=(), repos=(), branches=None, forks=True, archived=True, **kwargs):
        super().__init__(context, **kwargs)
        self.orgs = list(orgs)
        self.repos = list(repos)
        self.branches = branches or {}
        self.forks = forks
        self.archived = archived
        self.clones = os.path.join(context.cache_dir, "repos")

    def repositories(self, org):
        """Return the organization's public repositories."""
        repos, page = [], 1
        headers = {"Accept": "application/vnd.github+json"}
        if token := os.getenv("GITHUB_TOKEN"):
            headers["Authorization"] = f"Bearer {token}"
        while True:
            response = self.context.session.get(
                f"https://api.github.com/orgs/{org}/repos",
                params={"type": "public", "per_page": PER_PAGE, "page": page},
                headers=headers,
                timeout=30,
            )
            response.raise_for_status()
            batch = response.json()
            repos += [
                r["full_name"]
                for r in batch
                if not r["private"] and (self.forks or not r["fork"]) and (self.archived or not r["archived"])
            ]
            if len(batch) < PER_PAGE:
                return repos
            page += 1

    def checkout(self, repo, branch):
        """Return (directory, branch name) for a shallow clone of the branch (default: the default branch)."""
        directory = os.path.join(self.clones, repo + (f"@{branch}" if branch else ""))
        if os.path.exists(directory):
            ref = branch or "HEAD"
            subprocess.run(["git", "-C", directory, "fetch", "-q", "--depth", "1", "origin", ref], check=True)  # noqa: S603, S607
            subprocess.run(["git", "-C", directory, "reset", "-q", "--hard", "FETCH_HEAD"], check=True)  # noqa: S603, S607
        else:
            options = ["--branch", branch] if branch else []
            url = f"https://github.com/{repo}.git"
            subprocess.run(["git", "clone", "-q", "--depth", "1", *options, url, directory], check=True)  # noqa: S603, S607
        if not branch:
            out = subprocess.run(  # noqa: S603
                ["git", "-C", directory, "ls-remote", "--symref", "origin", "HEAD"],  # noqa: S607
                capture_output=True,
                check=True,
            )
            branch = re.search(r"refs/heads/(\S+)\tHEAD", out.stdout.decode()).group(1)
        return directory, branch

    def files(self, directory):
        """Yield (path, Links) for each tracked text file and document in the clone."""
        out = subprocess.run(["git", "-C", directory, "ls-files", "-z"], capture_output=True, check=True)  # noqa: S603, S607
        for path in out.stdout.decode().split("\0"):
            full = os.path.join(directory, path)
            if not path or not os.path.isfile(full) or os.path.islink(full):
                continue
            if path.lower().endswith(DOCUMENTS):
                found, error = self.extractor.file(full)
                if error:
                    logger.warning("%s: %s", full, error)
                yield path, found
            # Larger text files are data, like test fixtures, rather than pages.
            elif os.path.getsize(full) <= self.context.max_text_bytes:
                with open(full, "rb") as f:
                    data = f.read()
                if b"\0" not in data[:8000]:
                    yield path, self.extractor.text(data.decode("utf-8", "replace"), kind_of(path))
            else:
                self.skipped_for_size += 1
                logger.info("%s: skipped: larger than %d bytes", full, self.context.max_text_bytes)

    def links(self):
        repos = [repo for org in self.orgs for repo in self.repositories(org)] + self.repos
        for repo in dict.fromkeys(repos):
            for wanted in self.branches.get(repo, [None]):
                directory, branch = self.checkout(repo, wanted)
                n = 0
                for path, found in self.files(directory):
                    location = f"https://github.com/{repo}/blob/{branch}/{path}"
                    for link in found:
                        n += 1
                        yield dataclasses.replace(link, location=location)
                if n:
                    logger.info("%s@%s: %d links", repo, branch, n)
