"""GitHub URL parsing, commit resolution and tarball download.

Repo contents are only ever read as data: the tarball is streamed to a temp file and its
members are read in memory. Nothing from the repo is extracted to disk or executed.
"""

import re
import tarfile
import tempfile
from dataclasses import dataclass, field
from typing import Callable, Iterator
from urllib.parse import quote, unquote

import httpx

from viva import config

API = "https://api.github.com"
_OWNER = re.compile(r"[A-Za-z0-9](?:[A-Za-z0-9-]{0,38})")
_REPO = re.compile(r"[A-Za-z0-9_.-]{1,100}")
# Branch names may contain "/", so a URL like /tree/feature/x/src is ambiguous. We try
# prefixes shortest-first (git forbids both "feature" and "feature/x" existing), capped.
_MAX_REF_ATTEMPTS = 4


class BadRepoURL(ValueError):
    pass


class GitHubError(RuntimeError):
    pass


@dataclass
class RepoURL:
    owner: str
    name: str
    rest: list[str] = field(default_factory=list)  # segments after /tree/ or /blob/: ref + subpath
    is_blob: bool = False


def parse_repo_url(url: str) -> RepoURL:
    """Parse any reasonable form of a GitHub repo link. Does not touch the network."""
    s = url.strip()
    if not s:
        raise BadRepoURL("Paste a GitHub repo link.")
    m = re.fullmatch(r"git@github\.com:([^/]+)/([^/]+?)(?:\.git)?/?", s)
    if m:
        return _validated(m[1], m[2], [])
    s = re.sub(r"^(?:https?://)?(?:www\.)?", "", s, flags=re.I)
    s = s.split("#", 1)[0].split("?", 1)[0]
    if s.lower().startswith("github.com/"):
        s = s[len("github.com/"):]
    elif "." in s.split("/", 1)[0]:
        raise BadRepoURL("Only github.com repo links are supported.")
    parts = [p for p in unquote(s).split("/") if p]
    if len(parts) < 2:
        raise BadRepoURL("That doesn't look like a repo link. Expected github.com/owner/repo.")
    owner, name, rest = parts[0], parts[1], parts[2:]
    if name.lower().endswith(".git"):
        name = name[:-4]
    if len(rest) >= 2 and rest[0] in ("tree", "blob"):
        return _validated(owner, name, rest[1:], is_blob=rest[0] == "blob")
    if len(rest) >= 2 and rest[0] == "commit":
        return _validated(owner, name, rest[1:2])
    return _validated(owner, name, [])  # repo root, or a page like /issues


def _validated(owner: str, name: str, rest: list[str], is_blob: bool = False) -> RepoURL:
    if not _OWNER.fullmatch(owner) or not _REPO.fullmatch(name) or name in (".", ".."):
        raise BadRepoURL(f"'{owner}/{name}' isn't a valid GitHub repo name.")
    return RepoURL(owner, name, rest, is_blob)


def split_ref_and_subpath(
    rest: list[str], is_blob: bool, sha_for: Callable[[str], str | None]
) -> tuple[str | None, str, str]:
    """Return (ref, commit_sha, subpath). `sha_for` resolves a ref to a SHA or None."""
    if not rest:
        sha = sha_for("HEAD")
        if not sha:
            raise GitHubError("Repo not found. Is it public?")
        return None, sha, ""
    for i in range(1, min(len(rest), _MAX_REF_ATTEMPTS) + 1):
        ref = "/".join(rest[:i])
        sha = sha_for(ref)
        if sha:
            subpath = "/".join(rest[i:])
            if is_blob:  # link to a file: interview about the folder it lives in
                subpath = subpath.rsplit("/", 1)[0] if "/" in subpath else ""
            return ref, sha, subpath
    if not sha_for("HEAD"):
        raise GitHubError("Repo not found. Is it public?")
    raise GitHubError(f"Couldn't find a branch, tag or commit called '{rest[0]}'.")


class GitHub:
    def __init__(self, token: str | None = config.GITHUB_TOKEN):
        headers = {
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
            "User-Agent": "viva-interviewer",
        }
        if token:
            headers["Authorization"] = f"Bearer {token}"
        self.http = httpx.Client(base_url=API, headers=headers, timeout=30, follow_redirects=True)

    def _check(self, r: httpx.Response) -> None:
        if r.status_code in (403, 429) and (
            r.headers.get("x-ratelimit-remaining") == "0" or r.status_code == 429
        ):
            raise GitHubError("GitHub is rate-limiting requests right now. Try again in a few minutes.")
        if r.status_code == 409:
            raise GitHubError("That repo is empty.")
        if r.status_code >= 500:
            raise GitHubError(f"GitHub returned an error ({r.status_code}). Try again shortly.")

    def sha_for(self, owner: str, name: str, ref: str) -> str | None:
        r = self.http.get(
            f"/repos/{owner}/{name}/commits/{quote(ref, safe='/')}",
            headers={"Accept": "application/vnd.github.sha"},
        )
        self._check(r)
        if r.status_code in (404, 422):
            return None
        if r.status_code != 200:
            raise GitHubError(f"GitHub returned {r.status_code} resolving '{ref}'.")
        return r.text.strip()

    def resolve(self, url: RepoURL) -> tuple[str | None, str, str]:
        return split_ref_and_subpath(
            url.rest, url.is_blob, lambda ref: self.sha_for(url.owner, url.name, ref)
        )

    def download_tarball(self, owner: str, name: str, sha: str) -> str:
        """Stream the tarball for a commit to a temp file and return its path."""
        out = tempfile.NamedTemporaryFile(prefix="viva-", suffix=".tar.gz", delete=False)
        total = 0
        with out, self.http.stream("GET", f"/repos/{owner}/{name}/tarball/{sha}", timeout=120) as r:
            self._check(r)
            if r.status_code != 200:
                raise GitHubError(f"Couldn't download the repo ({r.status_code}).")
            for chunk in r.iter_bytes(1 << 16):
                total += len(chunk)
                if total > config.MAX_TARBALL_BYTES:
                    raise GitHubError("That repo is too large to interview about (over 150 MB).")
                out.write(chunk)
        return out.name

    def commit_messages(self, owner: str, name: str, sha: str, subpath: str = "", n: int = 50) -> list[str]:
        params = {"sha": sha, "per_page": n}
        if subpath:
            params["path"] = subpath
        r = self.http.get(f"/repos/{owner}/{name}/commits", params=params)
        self._check(r)
        if r.status_code != 200:
            return []
        return [c["commit"]["message"].strip()[:300] for c in r.json()]


def iter_tarball(tar_path: str) -> Iterator[tuple[str, int, Callable[[], bytes]]]:
    """Yield (repo-relative path, size, reader) for every regular file in the tarball.

    Symlinks, devices and directories are skipped; nothing is written to disk.
    """
    with tarfile.open(tar_path, "r:gz") as tar:
        for member in tar:
            if not member.isfile() or "/" not in member.name:
                continue
            path = member.name.split("/", 1)[1]  # drop the "owner-repo-sha/" prefix
            yield path, member.size, lambda m=member: tar.extractfile(m).read()


def blob_url(owner: str, name: str, sha: str, path: str, start: int | None = None, end: int | None = None) -> str:
    url = f"https://github.com/{owner}/{name}/blob/{sha}/{quote(path)}"
    if start:
        url += f"#L{start}" + (f"-L{end}" if end and end != start else "")
    return url
