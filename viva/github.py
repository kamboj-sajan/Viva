"""GitHub URL parsing, commit resolution and tarball download.

Repo contents are only ever read as data: the tarball is streamed to a temp file and its
members are read in memory. Nothing from the repo is extracted to disk or executed.
"""

import re
import tarfile
import tempfile
from xml.etree import ElementTree
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
    """Public-repo access that works without a token.

    Refs come from git's smart-HTTP endpoint and tarballs from codeload, neither of which counts
    against the REST API's 60 calls/hour for anonymous clients (shared cloud IPs often exhaust that).
    The REST API is used only with a token (commit messages, short SHAs).
    """

    def __init__(self, token: str | None = config.GITHUB_TOKEN):
        self.token = token
        headers = {"Accept": "application/vnd.github+json", "X-GitHub-Api-Version": "2022-11-28", "User-Agent": "viva-interviewer"}
        if token:
            headers["Authorization"] = f"Bearer {token}"
        self.api = httpx.Client(base_url=API, headers=headers, timeout=30, follow_redirects=True)
        self.web = httpx.Client(headers={"User-Agent": "git/2.45 viva-interviewer"}, timeout=60, follow_redirects=True)
        self._refs: dict[tuple[str, str], dict[str, str] | None] = {}

    def _check(self, r: httpx.Response) -> None:
        if r.status_code == 429 or (r.status_code == 403 and r.headers.get("x-ratelimit-remaining") == "0"):
            raise GitHubError("GitHub is rate-limiting requests right now. Try again in a few minutes.")
        if r.status_code >= 500:
            raise GitHubError(f"GitHub returned an error ({r.status_code}). Try again shortly.")

    def refs(self, owner: str, name: str) -> dict[str, str] | None:
        """{ref name: commit sha} including "HEAD", or None if the repo doesn't exist or is private."""
        key = (owner.lower(), name.lower())
        if key not in self._refs:
            r = self.web.get(f"https://github.com/{owner}/{name}.git/info/refs", params={"service": "git-upload-pack"})
            self._check(r)
            self._refs[key] = parse_refs(r.content) if r.status_code == 200 else None
        return self._refs[key]

    def sha_for(self, owner: str, name: str, ref: str) -> str | None:
        refs = self.refs(owner, name)
        if refs is None:
            return None
        if ref == "HEAD":
            return refs.get("HEAD")
        if re.fullmatch(r"[0-9a-f]{40}", ref):
            return ref  # a full commit SHA; the download verifies it exists
        for candidate in (f"refs/heads/{ref}", f"refs/tags/{ref}^{{}}", f"refs/tags/{ref}"):
            if candidate in refs:
                return refs[candidate]
        if self.token and re.fullmatch(r"[0-9a-f]{7,39}", ref):  # short SHA: only the API can expand it
            r = self.api.get(f"/repos/{owner}/{name}/commits/{ref}", headers={"Accept": "application/vnd.github.sha"})
            self._check(r)
            return r.text.strip() if r.status_code == 200 else None
        return None

    def resolve(self, url: RepoURL) -> tuple[str | None, str, str]:
        return split_ref_and_subpath(url.rest, url.is_blob, lambda ref: self.sha_for(url.owner, url.name, ref))

    def download_tarball(self, owner: str, name: str, sha: str) -> str:
        """Stream the tarball for a commit to a temp file and return its path."""
        out = tempfile.NamedTemporaryFile(prefix="viva-", suffix=".tar.gz", delete=False)
        total = 0
        with out, self.web.stream("GET", f"https://codeload.github.com/{owner}/{name}/tar.gz/{sha}", timeout=120) as r:
            self._check(r)
            if r.status_code != 200:
                raise GitHubError(f"Couldn't download the repo ({r.status_code}). Is it public?")
            for chunk in r.iter_bytes(1 << 16):
                total += len(chunk)
                if total > config.MAX_TARBALL_BYTES:
                    raise GitHubError("That repo is too large to interview about (over 150 MB).")
                out.write(chunk)
        return out.name

    def commit_messages(self, owner: str, name: str, sha: str, subpath: str = "", n: int = 50) -> list[str]:
        """Recent commit messages: 50 through the API with a token, otherwise the last 20 from the Atom feed."""
        try:
            if self.token:
                params = {"sha": sha, "per_page": n} | ({"path": subpath} if subpath else {})
                r = self.api.get(f"/repos/{owner}/{name}/commits", params=params)
                return [c["commit"]["message"].strip()[:300] for c in r.json()] if r.status_code == 200 else []
            r = self.web.get(f"https://github.com/{owner}/{name}/commits/{sha}.atom")
            if r.status_code != 200:
                return []
            ns = {"a": "http://www.w3.org/2005/Atom"}
            return [(e.findtext("a:title", "", ns) or "").strip()[:300] for e in ElementTree.fromstring(r.content).findall("a:entry", ns)]
        except (httpx.HTTPError, ElementTree.ParseError, ValueError):
            return []  # commit history only feeds the overview; never fail ingestion over it


def parse_refs(body: bytes) -> dict[str, str]:
    """Parse git's smart-HTTP ref advertisement (pkt-line format)."""
    refs: dict[str, str] = {}
    pos = 0
    while pos + 4 <= len(body):
        try:
            size = int(body[pos:pos + 4], 16)
        except ValueError:
            break
        if size == 0:
            pos += 4
            continue
        line = body[pos + 4:pos + size].decode("utf-8", "replace").rstrip("\n")
        pos += size
        if line.startswith("#"):
            continue
        line, _, caps = line.partition("\0")
        sha, _, ref = line.partition(" ")
        if re.fullmatch(r"[0-9a-f]{40}", sha) and ref and not sha.startswith("0" * 40):
            refs[ref] = sha
        m = re.search(r"symref=HEAD:(\S+)", caps)
        if m and "HEAD" not in refs and m.group(1) in refs:
            refs["HEAD"] = refs[m.group(1)]
    return refs


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
