"""Split a source file into chunks (function, class, module glue) with exact 1-based line ranges.

Invariant, enforced by tests: chunk.text == "\\n".join(lines[start - 1:end]) where
lines = text.split("\\n"). We split on "\\n" only: str.splitlines() also breaks on form feeds
and other separators, which would disagree with GitHub's line numbers.

Chunks partition the file: they never overlap, and the only lines left out are blank lines
and lone closing brackets.
"""

import importlib
import re
from dataclasses import dataclass
from functools import cache
from typing import Callable

import tree_sitter as ts

MAX_LINES = 120  # a block longer than this is split into its members, or into windows
WINDOW = 80
MIN_BLOCK = 3  # shorter top-level nodes are grouped with their neighbours

GRAMMARS = {
    "python": ("tree_sitter_python", "language"),
    "javascript": ("tree_sitter_javascript", "language"),
    "typescript": ("tree_sitter_typescript", "language_typescript"),
    "tsx": ("tree_sitter_typescript", "language_tsx"),
    "go": ("tree_sitter_go", "language"),
    "rust": ("tree_sitter_rust", "language"),
    "java": ("tree_sitter_java", "language"),
    "c": ("tree_sitter_c", "language"),
    "cpp": ("tree_sitter_cpp", "language"),
    "csharp": ("tree_sitter_c_sharp", "language"),
    "ruby": ("tree_sitter_ruby", "language"),
    "php": ("tree_sitter_php", "language_php"),
    "bash": ("tree_sitter_bash", "language"),
    "kotlin": ("tree_sitter_kotlin", "language"),
    "scala": ("tree_sitter_scala", "language"),
    "swift": ("tree_sitter_swift", "language"),
}
HASH_COMMENT_LANGS = {"python", "ruby", "bash", "r", "yaml", "toml", "make", "dockerfile", "elixir", "julia", "tcl", "hcl", "procfile", "text"}
_CLOSER = re.compile(r"^\s*([})\]]+[;,)]*|end|\*/)\s*$")


@dataclass
class Chunk:
    start: int  # 1-based, inclusive
    end: int  # 1-based, inclusive
    kind: str  # function | class | block | module | window
    symbol: str | None
    text: str


class _Block:
    """A syntactic unit with a line span and lazily computed members."""

    __slots__ = ("start", "end", "kind", "name", "_members")

    def __init__(self, start: int, end: int, kind: str, name: str | None, members: Callable[[], list["_Block"]] | list):
        self.start, self.end, self.kind, self.name, self._members = start, end, kind, name, members

    def members(self) -> list["_Block"]:
        return self._members() if callable(self._members) else self._members


@cache
def _language(lang: str) -> ts.Language | None:
    if lang not in GRAMMARS:
        return None
    module, fn = GRAMMARS[lang]
    return ts.Language(getattr(importlib.import_module(module), fn)())


def chunk_file(text: str, language: str | None) -> list[Chunk]:
    lines = text.split("\n")
    if text.endswith("\n"):
        lines = lines[:-1]  # the trailing newline does not start a new line
    if not lines:
        return []
    hash_comments = language in HASH_COMMENT_LANGS
    if language in ("verilog", "systemverilog"):
        segments = _partition(1, len(lines), _hdl_blocks(lines), lines, hash_comments, None, 0)
    elif (lang := _language(language or "")) is not None:
        src = text.encode("utf-8")  # must outlive every node: nodes point into this buffer
        tree = ts.Parser(lang).parse(src)
        tops = [_ts_block(n, src) for n in tree.root_node.named_children if "comment" not in n.type]
        segments = _partition(1, len(lines), tops, lines, hash_comments, None, 0)
    else:
        segments = [(s, e, "window", None) for s, e in _windows(1, len(lines), lines)]

    chunks = []
    for s, e, kind, symbol in segments:
        while s <= e and not lines[s - 1].strip():
            s += 1
        while e >= s and not lines[e - 1].strip():
            e -= 1
        body = lines[s - 1:e]
        if s > e or all(_CLOSER.match(line) or not line.strip() for line in body):
            continue
        if kind in ("module", "block") and e - s < 2 and all(_is_comment(line, hash_comments) for line in body):
            continue  # section-divider comments carry nothing worth retrieving
        chunks.append(Chunk(s, e, kind, symbol, "\n".join(lines[s - 1:e])))
    return chunks


def symbols(chunks: list[Chunk], limit: int = 40) -> list[str]:
    """Distinct definition names, in file order."""
    seen: dict[str, None] = {}
    for c in chunks:
        if c.symbol and c.kind in ("function", "class"):
            seen.setdefault(c.symbol, None)
    return list(seen)[:limit]


def _partition(lo, hi, members, lines, hash_comments, prefix, depth) -> list[tuple[int, int, str, str | None]]:
    """Cover lines lo..hi with segments: big members become their own segment, the rest is glue."""
    out = []
    cur = lo
    for m in members:
        s, e = max(m.start, cur), min(m.end, hi)
        if e - s + 1 < MIN_BLOCK:
            continue
        while s - 1 >= cur and _is_comment(lines[s - 2], hash_comments):  # attach doc comments
            s -= 1
        if s > cur:
            out += _glue(cur, s - 1, lines, prefix)
        name = f"{prefix}.{m.name}" if prefix and m.name else (m.name or prefix)
        if e - s + 1 > MAX_LINES:
            kids = m.members() if depth < 3 else []
            if kids:
                out += _partition(s, e, kids, lines, hash_comments, name, depth + 1)
            else:
                out += [(a, b, "window", name) for a, b in _windows(s, e, lines)]
        else:
            out.append((s, e, m.kind, name))
        cur = e + 1
    if cur <= hi:
        out += _glue(cur, hi, lines, prefix)
    return out


def _glue(a, b, lines, prefix):
    kind = "block" if prefix else "module"
    return [(s, e, kind, prefix) for s, e in _windows(a, b, lines)]


def _windows(a: int, b: int, lines: list[str]) -> list[tuple[int, int]]:
    """Cut a..b into pieces of at most WINDOW lines, preferring to break after a blank line."""
    out = []
    i = a
    while i <= b:
        j = min(b, i + WINDOW - 1)
        if j < b:
            for k in range(j, i + int(WINDOW * 0.6) - 1, -1):
                if not lines[k - 1].strip():
                    j = k
                    break
        out.append((i, j))
        i = j + 1
    return out


def _is_comment(line: str, hash_comments: bool) -> bool:
    s = line.lstrip()
    if hash_comments:
        return s.startswith("#") and not s.startswith("#!")
    return s.startswith(("//", "/*", "*", "--"))


# --- tree-sitter ----------------------------------------------------------------------------


def _ts_block(node: ts.Node, src: bytes) -> _Block:
    start = node.start_point.row + 1
    end_row = node.end_point.row
    if node.end_point.column == 0 and end_row > node.start_point.row:
        end_row -= 1  # node ends with the newline: the next line is not part of it
    inner = _unwrap(node)
    return _Block(start, end_row + 1, _kind(inner.type), _ts_name(inner, src), lambda: _ts_members(inner, src))


def _unwrap(node: ts.Node) -> ts.Node:
    """decorated_definition / export_statement wrap the real definition."""
    for field in ("definition", "declaration"):
        inner = node.child_by_field_name(field)
        if inner is not None and node.type in ("decorated_definition", "export_statement"):
            return inner
    return node


def _ts_members(node: ts.Node, src: bytes) -> list[_Block]:
    body = node
    for _ in range(4):
        b = body.child_by_field_name("body")
        if b is not None:
            body = b
            break
        kids = [c for c in body.named_children if "comment" not in c.type]
        if not kids:
            break
        biggest = max(kids, key=lambda c: c.end_point.row - c.start_point.row)
        if biggest.end_point.row - biggest.start_point.row < 0.5 * (node.end_point.row - node.start_point.row):
            break
        body = biggest
    if body is node:
        return []
    return [_ts_block(c, src) for c in body.named_children if "comment" not in c.type]


def _kind(t: str) -> str:
    if any(k in t for k in ("class", "struct", "impl", "interface", "trait", "enum", "object", "protocol", "record", "namespace", "module")):
        return "class"
    if any(k in t for k in ("function", "method", "constructor", "func", "lambda", "arrow")):
        return "function"
    return "block"


_NAME_TYPES = {"identifier", "field_identifier", "qualified_identifier", "type_identifier", "destructor_name", "operator_name", "simple_identifier", "name", "constant"}


def _ts_name(node: ts.Node, src: bytes) -> str | None:
    n = node.child_by_field_name("name")
    if n is None and node.type in ("lexical_declaration", "variable_declaration"):
        d = next((c for c in node.named_children if c.type == "variable_declarator"), None)
        n = d.child_by_field_name("name") if d is not None else None
    if n is None and node.child_by_field_name("declarator") is not None:  # C/C++ functions
        d = node.child_by_field_name("declarator")
        while d is not None and d.type not in _NAME_TYPES:
            d = d.child_by_field_name("declarator")
        n = d
    if n is None:  # Go type_declaration -> type_spec, Kotlin class -> type_identifier, ...
        for c in node.named_children:
            if c.type in _NAME_TYPES:
                n = c
                break
            if c.child_by_field_name("name") is not None:
                n = c.child_by_field_name("name")
                break
    if n is None:
        return None
    name = src[n.start_byte:n.end_byte].decode("utf-8", "replace").split("\n")[0].strip()
    return name[:80] or None


# --- Verilog / SystemVerilog ----------------------------------------------------------------

_HDL_START = re.compile(
    r"^\s*(?:(?:virtual|static|automatic|extern|pure|local|protected|interface)\s+)*"
    r"(module|macromodule|interface|package|class|program|function|task|primitive|checker)\b"
)
_HDL_END = re.compile(r"^\s*(endmodule|endinterface|endpackage|endclass|endprogram|endfunction|endtask|endprimitive|endchecker)\b")
_HDL_PROC = re.compile(r"^\s*(always_ff|always_comb|always_latch|always|initial|final|generate)\b")
_HDL_BEGIN = re.compile(r"\b(begin|fork)\b")
_HDL_ENDKW = re.compile(r"\b(end|join|join_any|join_none)\b")
_HDL_EXTERN = re.compile(r"^\s*(extern|pure\s+virtual)\b")


def _hdl_name(kind: str, line: str) -> str | None:
    if kind in ("function", "task"):
        m = re.search(r"(\w+)\s*(?:\(|;)", line.split("//")[0])
    else:
        m = re.search(rf"\b{kind}\s+(?:automatic\s+|static\s+)?(\w+)", line)
    return m.group(1) if m else None


def _hdl_blocks(lines: list[str]) -> list[_Block]:
    """Find module/class/function/... blocks by matching start and end keywords."""
    stack: list[tuple[str, str | None, int, list]] = []
    top: list[_Block] = []
    for i, raw in enumerate(lines, 1):
        line = raw.split("//")[0]
        m = _HDL_START.match(line)
        if m and not _HDL_EXTERN.match(line) and not (m.group(1) == "interface" and "class" in line):
            kind = m.group(1)
            if kind == "macromodule":
                kind = "module"
            stack.append((kind, _hdl_name(kind, line), i, []))
            continue
        m = _HDL_END.match(line)
        if m and stack and m.group(1) == "end" + stack[-1][0]:
            kind, name, start, kids = stack.pop()
            if kind == "module":
                kids = sorted(kids + _hdl_procs(lines, start, i, kids), key=lambda b: b.start)
            block = _Block(start, i, "class" if kind in ("module", "interface", "package", "class", "program") else "function", name, kids)
            (stack[-1][3] if stack else top).append(block)
    return top


def _hdl_procs(lines: list[str], lo: int, hi: int, taken: list[_Block]) -> list[_Block]:
    """always/initial/generate blocks inside a module, sized by begin/end balance."""
    out = []
    i = lo + 1
    while i < hi:
        if any(b.start <= i <= b.end for b in taken) or not _HDL_PROC.match(lines[i - 1]):
            i += 1
            continue
        end = _hdl_stmt_end(lines, i, hi - 1)
        kw = _HDL_PROC.match(lines[i - 1]).group(1)
        out.append(_Block(i, end, "block", kw, []))
        i = end + 1
    return out


def _hdl_stmt_end(lines: list[str], i: int, limit: int) -> int:
    if _HDL_PROC.match(lines[i - 1]).group(1) == "generate":
        for k in range(i, limit + 1):
            if re.match(r"^\s*endgenerate\b", lines[k - 1]):
                return k
        return i
    depth, seen = 0, False
    for k in range(i, limit + 1):
        code = lines[k - 1].split("//")[0]
        b, e = len(_HDL_BEGIN.findall(code)), len(_HDL_ENDKW.findall(code))
        seen = seen or b > 0
        depth += b - e
        if seen and depth <= 0:
            return k
        if not seen and ";" in code:
            return k
    return limit
