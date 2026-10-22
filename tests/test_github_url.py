import pytest

from viva.github import BadRepoURL, GitHubError, blob_url, parse_repo_url, split_ref_and_subpath


@pytest.mark.parametrize(
    "url, owner, name, rest, is_blob",
    [
        ("https://github.com/psf/requests", "psf", "requests", [], False),
        ("http://www.github.com/psf/requests/", "psf", "requests", [], False),
        ("github.com/psf/requests", "psf", "requests", [], False),
        ("psf/requests", "psf", "requests", [], False),
        ("  https://github.com/psf/requests.git  ", "psf", "requests", [], False),
        ("git@github.com:psf/requests.git", "psf", "requests", [], False),
        ("https://github.com/psf/requests#readme", "psf", "requests", [], False),
        ("https://github.com/psf/requests?tab=readme", "psf", "requests", [], False),
        ("https://github.com/psf/requests/issues", "psf", "requests", [], False),
        ("https://github.com/psf/requests/tree/main", "psf", "requests", ["main"], False),
        ("https://github.com/psf/requests/tree/main/src/requests", "psf", "requests", ["main", "src", "requests"], False),
        ("https://github.com/o/r/tree/feature/x/src", "o", "r", ["feature", "x", "src"], False),
        ("https://github.com/o/r/blob/main/src/app.py", "o", "r", ["main", "src", "app.py"], True),
        ("https://github.com/o/r/tree/fix%2Fbug/lib", "o", "r", ["fix", "bug", "lib"], False),
        ("https://github.com/o/my.repo-name_2", "o", "my.repo-name_2", [], False),
        ("https://github.com/o/r/commit/abc123", "o", "r", ["abc123"], False),
    ],
)
def test_parse(url, owner, name, rest, is_blob):
    u = parse_repo_url(url)
    assert (u.owner, u.name, u.rest, u.is_blob) == (owner, name, rest, is_blob)


@pytest.mark.parametrize(
    "url",
    ["", "   ", "https://gitlab.com/o/r", "bitbucket.org/o/r", "github.com/onlyowner", "https://github.com/", "github.com/-bad/r", "github.com/o/.."],
)
def test_parse_rejects(url):
    with pytest.raises(BadRepoURL):
        parse_repo_url(url)


REFS = {"HEAD": "sha_head", "main": "sha_main", "feature/x": "sha_fx", "v1.0": "sha_tag"}


def resolve(rest, is_blob=False, refs=REFS):
    calls = []

    def sha_for(ref):
        calls.append(ref)
        return refs.get(ref)

    return split_ref_and_subpath(rest, is_blob, sha_for), calls


def test_default_branch():
    assert resolve([])[0] == (None, "sha_head", "")


def test_branch_and_subfolder():
    (ref, sha, sub), calls = resolve(["main", "src", "app"])
    assert (ref, sha, sub) == ("main", "sha_main", "src/app")
    assert calls == ["main"]  # one API call for the common case


def test_branch_with_slash():
    assert resolve(["feature", "x", "src"])[0] == ("feature/x", "sha_fx", "src")


def test_tag_and_commit():
    assert resolve(["v1.0"])[0] == ("v1.0", "sha_tag", "")


def test_blob_uses_containing_folder():
    assert resolve(["main", "src", "app.py"], is_blob=True)[0] == ("main", "sha_main", "src")
    assert resolve(["main", "README.md"], is_blob=True)[0] == ("main", "sha_main", "")


def test_unknown_branch():
    with pytest.raises(GitHubError, match="nope"):
        resolve(["nope", "src"])


def test_missing_repo():
    with pytest.raises(GitHubError, match="not found"):
        resolve(["main"], refs={})
    with pytest.raises(GitHubError, match="not found"):
        resolve([], refs={})


def test_blob_url():
    assert blob_url("o", "r", "abc", "src/a b.py", 3, 9) == "https://github.com/o/r/blob/abc/src/a%20b.py#L3-L9"
    assert blob_url("o", "r", "abc", "x.py", 5, 5).endswith("#L5")
