import base64
import dataclasses
import logging
import os
import re
import subprocess
from pathlib import Path

from walkrightin.extract import DOCUMENTS, kind_of
from walkrightin.sources import Source

logger = logging.getLogger(__name__)

PER_PAGE = 100
# A page on GitHub, as a location, so that a consumer can find its repository, branch and path.
PAGE = re.compile(r"https://github\.com/([^/]+/[^/]+)/blob/([^/]+)/(.+)")


class GitHubSource(Source):
    """
    The tracked files of public GitHub repositories: text files, and PDF, Office and OpenDocument files.

    ``orgs`` lists organizations, whose public repositories are read, including forks and archived ones. ``repos``
    lists other repositories, and ``exclude`` repositories not to read. Each repository's default branch is read, plus
    any in ``branches`` ({repo: [branch]}). Each link's location is the file's page on GitHub. Clones are shallow, in
    the cache directory. Set GITHUB_TOKEN to raise GitHub's rate limit.

    With ``include_private``, the organizations' private repositories are read too, like those of public websites
    whose source is private. GITHUB_TOKEN must then be able to read them; it's passed to git in its environment.
    """

    type = "github"

    def __init__(
        self,
        context,
        *,
        orgs=(),
        repos=(),
        exclude=(),
        branches=None,
        forks=True,
        archived=True,
        include_private=False,
        **kwargs,
    ):
        super().__init__(context, **kwargs)
        self.orgs = list(orgs)
        self.repos = list(repos)
        self.exclude = set(exclude)
        self.branches = branches or {}
        self.forks = forks
        self.archived = archived
        self.clones = Path(context.cache_dir) / "repos"
        self.include_private = include_private
        self.token = os.getenv("GITHUB_TOKEN")
        if include_private and not self.token:
            msg = "include_private requires GITHUB_TOKEN"
            raise ValueError(msg)

    def repositories(self, org):
        """Return the organization's public repositories, and its private ones with ``include_private``."""
        repos, page = [], 1
        headers = {"Accept": "application/vnd.github+json"}
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"
        while True:
            response = self.context.session.get(
                f"https://api.github.com/orgs/{org}/repos",
                params={"type": "all" if self.include_private else "public", "per_page": PER_PAGE, "page": page},
                headers=headers,
                timeout=30,
            )
            response.raise_for_status()
            batch = response.json()
            repos += [
                r["full_name"]
                for r in batch
                if (self.include_private or not r["private"])
                and (self.forks or not r["fork"])
                and (self.archived or not r["archived"])
            ]
            if len(batch) < PER_PAGE:
                return repos
            page += 1

    def git(self, *args, **kwargs):
        """Run git. With ``include_private``, authenticate with the token in git's environment, not its arguments."""
        env = None
        if self.include_private:
            credentials = base64.b64encode(f"x-access-token:{self.token}".encode()).decode()
            env = {
                **os.environ,
                "GIT_CONFIG_COUNT": "1",
                "GIT_CONFIG_KEY_0": "http.https://github.com/.extraheader",
                "GIT_CONFIG_VALUE_0": f"AUTHORIZATION: basic {credentials}",
            }
        return subprocess.run(["git", *args], check=True, env=env, **kwargs)  # noqa: S603, S607

    def checkout(self, repo, branch):
        """Return (directory, branch name) for a shallow clone of the branch (default: the default branch)."""
        directory = self.clones / (repo + (f"@{branch}" if branch else ""))
        if directory.exists():
            ref = branch or "HEAD"
            self.git("-C", directory, "fetch", "-q", "--depth", "1", "origin", ref)
            self.git("-C", directory, "reset", "-q", "--hard", "FETCH_HEAD")
        else:
            options = ["--branch", branch] if branch else []
            url = f"https://github.com/{repo}.git"
            self.git("clone", "-q", "--depth", "1", *options, url, directory)
        if not branch:
            out = self.git("-C", directory, "ls-remote", "--symref", "origin", "HEAD", capture_output=True)
            branch = re.search(r"refs/heads/(\S+)\tHEAD", out.stdout.decode()).group(1)
        return directory, branch

    def files(self, directory):
        """Yield (path, Links) for each tracked text file and document in the clone."""
        out = subprocess.run(["git", "-C", directory, "ls-files", "-z"], capture_output=True, check=True)  # noqa: S603, S607
        for path in out.stdout.decode().split("\0"):
            full = Path(directory) / path
            if not path or not full.is_file() or full.is_symlink():
                continue
            if path.lower().endswith(DOCUMENTS):
                found, error = self.extractor.file(full)
                if error:
                    logger.warning("%s: %s", full, error)
                yield path, found
            # Larger text files are data, like test fixtures, rather than pages.
            elif full.stat().st_size <= self.context.max_text_bytes:
                data = full.read_bytes()
                if b"\0" not in data[:8000]:
                    yield path, self.extractor.text(data.decode("utf-8", "replace"), kind_of(path))
            else:
                self.skipped_for_size += 1
                logger.info("%s: skipped: larger than %d bytes", full, self.context.max_text_bytes)

    def links(self):
        repos = [repo for org in self.orgs for repo in self.repositories(org)] + self.repos
        for repo in dict.fromkeys(r for r in repos if r not in self.exclude):
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
