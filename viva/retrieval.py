"""Hybrid search over a repo's chunks: identifier-aware keywords + vectors, merged with RRF."""

import re
from collections import defaultdict

from viva import db, embeddings

_IDENT = re.compile(r"[A-Za-z_][A-Za-z0-9_]*|\d+")
_CAMEL = re.compile(r"[A-Z]+(?=[A-Z][a-z])|[A-Z]?[a-z]+|[A-Z]+|\d+")
RRF_K = 60
CANDIDATES = 30
STOP = set(
    "a an and are as at be but by can do does did for from has have how i if in into is it its of on or "
    "so that the their then there these this to was what when where which while who why will with would "
    "you your we our they them me my not no yes about after before also any some just like get got".split()
)


def terms(text: str) -> list[str]:
    """Identifier-aware terms: parseHTTPRequest and parse_http_request both yield
    'parsehttprequest', 'parse', 'http', 'request'. Underscores are dropped because Postgres
    treats them as word separators."""
    out = []
    for tok in _IDENT.findall(text):
        whole = tok.replace("_", "").lower()
        if len(whole) > 1:
            out.append(whole)
        parts = [p.lower() for piece in tok.split("_") for p in _CAMEL.findall(piece)]
        if len(parts) > 1:
            out.extend(p for p in parts if len(p) > 1)
    return out


def index_text(text: str) -> str:
    return " ".join(terms(text))


def tsquery(text: str) -> str | None:
    words = [t for t in dict.fromkeys(terms(text)) if t not in STOP]
    return " | ".join(words[:64]) or None


def search(repo_id: int, text: str, k: int = 6, exclude_ids: tuple[int, ...] = ()) -> list[dict]:
    """Top-k chunks for free text. Falls back to keywords alone if embedding fails."""
    ranked: list[list[int]] = []
    if q := tsquery(text):
        rows = db.query(
            """SELECT id FROM chunks, to_tsquery('simple', %s) q
               WHERE repo_id = %s AND search_tsv @@ q
               ORDER BY ts_rank_cd(search_tsv, q) DESC LIMIT %s""",
            (q, repo_id, CANDIDATES),
        )
        ranked.append([r["id"] for r in rows])
    try:
        vec = embeddings.embed([text], input_type="query")[0]
    except embeddings.EmbeddingError:
        vec = None
    if vec is not None:
        rows = db.query(
            """SELECT id FROM chunks WHERE repo_id = %s AND vector_dims(embedding) = %s
               ORDER BY embedding <=> %s::vector LIMIT %s""",
            (repo_id, len(vec), db.vector(vec), CANDIDATES),
        )
        ranked.append([r["id"] for r in rows])

    scores: dict[int, float] = defaultdict(float)
    for ids in ranked:
        for rank, cid in enumerate(ids):
            scores[cid] += 1 / (RRF_K + rank + 1)
    top = [cid for cid in sorted(scores, key=lambda c: (-scores[c], c)) if cid not in exclude_ids][:k]
    if not top:
        return []
    rows = db.query(
        "SELECT id, path, kind, symbol, start_line, end_line, content FROM chunks WHERE id = ANY(%s)", (top,)
    )
    by_id = {r["id"]: r | {"score": round(scores[r["id"]], 5)} for r in rows}
    return [by_id[c] for c in top]
