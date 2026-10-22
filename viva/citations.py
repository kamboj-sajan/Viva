"""Evidence validation, done in code: a citation must name an ingested file, real lines, and a
quote that those lines actually contain (after collapsing whitespace).

When the quote is genuine but the model got the line numbers wrong, we relocate the citation to
where the quote really is — only if it occurs exactly once in the file, so the result is still an
exact excerpt of the code at code-computed lines.
"""

import bisect
import re
from dataclasses import dataclass

from viva import db

MAX_SPAN = 80  # a citation is a receipt, not a whole file
MIN_QUOTE = 8
_LINE_PREFIX = re.compile(r"^\s*\d+\s*\|\s?", re.M)  # "  42| code" copied from our numbered context
_ELLIPSIS = re.compile(r"\s*(?:\.\.\.|…)\s*")


@dataclass
class Checked:
    ok: bool
    path: str
    start_line: int
    end_line: int
    quote: str
    reason: str | None = None
    repaired: bool = False

    def evidence(self) -> dict:
        ev = {"path": self.path, "start_line": self.start_line, "end_line": self.end_line, "quote": self.quote}
        return ev | ({"repaired": True} if self.repaired else {})


def normalize(s: str) -> str:
    return re.sub(r"\s+", " ", s).strip()


def file_lines(text: str) -> list[str]:
    """Split like the chunker does: on "\\n" only, and a trailing newline doesn't start a line."""
    lines = text.split("\n")
    return lines[:-1] if text.endswith("\n") else lines


def load_files(repo_id: int) -> dict[str, list[str]]:
    rows = db.query("SELECT path, content FROM files WHERE repo_id = %s AND included", (repo_id,))
    return {r["path"]: file_lines(r["content"]) for r in rows}


def check(ev: dict, files: dict[str, list[str]]) -> Checked:
    path = str(ev.get("path", "")).strip()
    path = path[2:] if path.startswith("./") else path
    try:
        start, end = int(ev.get("start_line", 0)), int(ev.get("end_line", 0))
    except (TypeError, ValueError):
        start, end = 0, 0
    start, end = min(start, end), max(start, end)
    quote = _LINE_PREFIX.sub("", str(ev.get("quote", ""))).strip()
    nq = normalize(quote)

    def fail(reason: str) -> Checked:
        return Checked(False, path, start, end, quote, reason)

    lines = files.get(path)
    if lines is None:
        return fail("file not ingested")
    if len(_ELLIPSIS.sub("", nq)) < MIN_QUOTE:
        return fail("quote too short")
    in_range = 1 <= start and end <= len(lines)
    if in_range and end - start + 1 <= MAX_SPAN and _contains(normalize("\n".join(lines[start - 1:end])), nq):
        return Checked(True, path, start, end, quote)

    if not _ELLIPSIS.search(nq):
        hits = _locate(nq, lines)
        if len(hits) == 1:
            s, e = hits[0]
            return Checked(True, path, s, e, quote, repaired=True)
        if len(hits) > 1:
            return fail("quote is ambiguous and not in the cited lines")
    if not in_range:
        return fail("lines out of range")
    if end - start + 1 > MAX_SPAN:
        return fail("cited range too long")
    return fail("quote not found in cited lines")


def _contains(hay: str, nq: str) -> bool:
    """Substring match; a quote abbreviated with '...' must match its parts in order."""
    pos = 0
    for part in _ELLIPSIS.split(nq):
        if not part:
            continue
        i = hay.find(part, pos)
        if i < 0:
            return False
        pos = i + len(part)
    return True


def _locate(nq: str, lines: list[str]) -> list[tuple[int, int]]:
    """Every (start_line, end_line) where the normalized quote occurs in the normalized file."""
    starts, parts, offset = [], [], 0
    numbers = []
    for n, line in enumerate(lines, 1):
        t = normalize(line)
        if not t:
            continue
        starts.append(offset)
        numbers.append(n)
        parts.append(t)
        offset += len(t) + 1  # joined with single spaces
    joined = " ".join(parts)
    hits, i = [], joined.find(nq)
    while i >= 0:
        a = bisect.bisect_right(starts, i) - 1
        b = bisect.bisect_right(starts, i + len(nq) - 1) - 1
        hits.append((numbers[a], numbers[b]))
        i = joined.find(nq, i + 1)
    return hits
