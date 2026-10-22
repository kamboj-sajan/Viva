"""Ingestion pipeline: resolve -> download -> filter -> rank -> chunk -> embed + summarize -> map.

Progress is written to the repos row (stage + counts) so the CLI and the UI can both follow it.
"""

import os
import posixpath
import time
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from typing import Callable

from psycopg.types.json import Jsonb

from viva import chunking, config, db, embeddings, files, github, repomap, retrieval, usage

STALE_MINUTES = 10
Listener = Callable[[str, dict], None]


def prepare(url: str, force: bool = False) -> tuple[int, bool]:
    """Resolve the URL to a commit and find or create its repos row.

    Returns (repo_id, needs_run). needs_run is False on a cache hit or when another worker is
    already ingesting the same commit.
    """
    parsed = github.parse_repo_url(url)
    gh = github.GitHub()
    ref, sha, subpath = gh.resolve(parsed)
    owner, name = parsed.owner.lower(), parsed.name.lower()
    row = db.one(
        """INSERT INTO repos (owner, name, ref, subpath, commit_sha) VALUES (%s, %s, %s, %s, %s)
           ON CONFLICT (owner, name, commit_sha, subpath) DO UPDATE SET ref = COALESCE(repos.ref, EXCLUDED.ref)
           RETURNING id, status""",
        (owner, name, ref, subpath, sha),
    )
    if row["status"] == "ready" and not force:
        return row["id"], False
    claimed = db.one(
        """UPDATE repos SET status = 'ingesting', stage = 'queued', progress = '{}', error = NULL, updated_at = now()
           WHERE id = %s AND (status <> 'ingesting' OR updated_at < now() - make_interval(mins => %s) OR %s)
           RETURNING id""",
        (row["id"], STALE_MINUTES, force),
    )
    return row["id"], claimed is not None


def run(repo_id: int, listener: Listener | None = None) -> None:
    """Ingest a claimed repo row. Marks it failed (and re-raises) on error."""
    started = time.time()
    repo = db.one("SELECT * FROM repos WHERE id = %s", (repo_id,))

    def progress(stage: str | None = None, **counts) -> None:
        db.execute(
            "UPDATE repos SET stage = COALESCE(%s, stage), progress = progress || %s, updated_at = now() WHERE id = %s",
            (stage, Jsonb(counts), repo_id),
        )
        if listener:
            listener(stage or "", counts)

    try:
        _run(repo, progress)
        stats = db.one("SELECT stats FROM repos WHERE id = %s", (repo_id,))["stats"]
        stats |= {"seconds": round(time.time() - started, 1), "cost_usd": round(usage.spent_on_repo(repo_id), 4)}
        db.execute(
            "UPDATE repos SET status = 'ready', stage = 'ready', stats = %s, updated_at = now() WHERE id = %s",
            (Jsonb(stats), repo_id),
        )
        progress("ready")
    except Exception as e:
        db.execute(
            "UPDATE repos SET status = 'failed', error = %s, updated_at = now() WHERE id = %s", (str(e)[:500], repo_id)
        )
        raise


def _run(repo: dict, progress: Callable[..., None]) -> None:
    repo_id, owner, name, sha, subpath = repo["id"], repo["owner"], repo["name"], repo["commit_sha"], repo["subpath"]
    gh = github.GitHub()

    # 1. Download. The tarball is read in memory only; nothing is extracted or executed.
    progress("downloading")
    tar_path = gh.download_tarball(owner, name, sha)
    commits = gh.commit_messages(owner, name, sha, subpath)

    # 2. Filter.
    progress("filtering")
    skipped: Counter[str] = Counter()
    cands: list[files.Candidate] = []
    readmes: dict[str, str] = {}
    seen = 0
    prefix = subpath.strip("/") + "/" if subpath else ""
    try:
        for path, size, read in github.iter_tarball(tar_path):
            if files.is_readme(path) and posixpath.dirname(path) in ("", subpath.strip("/")) and size < 200_000:
                readmes[posixpath.dirname(path)] = files.decode(read()) or ""
            if prefix and not path.startswith(prefix):
                continue
            seen += 1
            reason = files.path_skip_reason(path, size)
            if reason is None:
                text = files.decode(read())
                reason = files.content_skip_reason(path, text)
            if reason:
                skipped[reason] += 1
                continue
            cands.append(files.Candidate(path, files.language_for(path) or "text", text, size, files.is_infra(path, text)))
            if seen % 200 == 0:
                progress(files_seen=seen, candidates=len(cands))
    finally:
        os.unlink(tar_path)
    if not cands:
        raise RuntimeError("No source files found to interview about.")
    readme = readmes.get(subpath.strip("/")) or readmes.get("", "")

    # 3. Rank and store.
    progress("ranking", files_seen=seen, candidates=len(cands))
    files.rank(cands, readme)
    included = [c for c in cands if c.included]
    with db.transaction() as conn:
        conn.execute("DELETE FROM files WHERE repo_id = %s", (repo_id,))  # cascades to chunks
        with conn.cursor() as cur:
            cur.executemany(
                """INSERT INTO files (repo_id, path, language, size_bytes, line_count, score, reasons, included, infra, content)
                   VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)""",
                [
                    (repo_id, c.path, c.language, c.size, c.line_count, c.score, c.reasons, c.included, c.infra,
                     c.text if c.included else None)
                    for c in cands
                ],
            )
    file_ids = {r["path"]: r["id"] for r in db.query("SELECT id, path FROM files WHERE repo_id = %s AND included", (repo_id,))}
    progress(files_included=len(included))

    # 4. Chunk.
    progress("chunking")
    chunk_rows, symbols = [], {}
    for c in included:
        chunks = chunking.chunk_file(c.text, c.language)
        symbols[c.path] = chunking.symbols(chunks)
        for ch in chunks:
            head = retrieval.index_text(f"{c.path} {ch.symbol or ''}")
            chunk_rows.append((repo_id, file_ids[c.path], c.path, ch.kind, ch.symbol, ch.start, ch.end, ch.text, head, retrieval.index_text(ch.text)))
    with db.transaction() as conn, conn.cursor() as cur:
        cur.executemany(
            """INSERT INTO chunks (repo_id, file_id, path, kind, symbol, start_line, end_line, content, search_tsv)
               VALUES (%s, %s, %s, %s, %s, %s, %s, %s,
                       setweight(to_tsvector('simple', %s), 'A') || to_tsvector('simple', %s))""",
            chunk_rows,
        )
        cur.executemany(
            "UPDATE files SET symbols = %s WHERE id = %s", [(symbols[p], file_ids[p]) for p in symbols]
        )
    progress(chunks=len(chunk_rows))

    # 5. Embed chunks and summarize files in parallel: they don't depend on each other.
    progress("embedding + summarizing", embedded=0, summarized=0, to_summarize=len(included))
    stored = db.query("SELECT id, path, symbol, content FROM chunks WHERE repo_id = %s ORDER BY id", (repo_id,))
    summary_input = [
        {"path": c.path, "language": c.language, "content": c.text, "symbols": symbols[c.path]} for c in included
    ]
    with ThreadPoolExecutor(max_workers=2) as pool:
        emb = pool.submit(
            embeddings.embed,
            [f"{r['path']}\n{r['symbol'] or ''}\n{r['content']}" for r in stored],
            "document",
            repo_id,
            lambda n: progress(embedded=n),
        )
        summ = pool.submit(
            repomap.summarize_files, summary_input, f"{owner}/{name}", repo_id, lambda n: progress(summarized=n)
        )
        vectors, summaries = emb.result(), summ.result()
    with db.transaction() as conn, conn.cursor() as cur:
        cur.executemany(
            "UPDATE chunks SET embedding = %s::vector WHERE id = %s",
            [(db.vector(v), r["id"]) for r, v in zip(stored, vectors)],
        )
        cur.executemany(
            "UPDATE files SET summary = %s, symbols = %s WHERE id = %s",
            [(s, keys + [x for x in symbols[p] if x not in keys], file_ids[p]) for p, (s, keys) in summaries.items()],
        )
    db.execute("UPDATE repos SET embed_model = %s WHERE id = %s", (config.EMBED_MODEL, repo_id))

    # 6. Architecture overview.
    progress("mapping")
    map_input = [
        {"path": c.path, "line_count": c.line_count, "summary": summaries.get(c.path, ("", []))[0], "symbols": symbols[c.path]}
        for c in included
    ]
    ov = repomap.overview(f"{owner}/{name}", repo_id, map_input, readme, commits)
    repo_map = {"overview": ov.model_dump(), "commits": [c.split("\n", 1)[0] for c in commits], "readme": readme[:20_000]}
    stats = {
        "files_seen": seen,
        "candidates": len(cands),
        "included": len(included),
        "chunks": len(chunk_rows),
        "skipped": dict(skipped.most_common()),
        "languages": dict(Counter(c.language for c in included).most_common()),
        "lines_included": sum(c.line_count for c in included),
    }
    db.execute(
        "UPDATE repos SET repo_map = %s, stats = %s WHERE id = %s", (Jsonb(repo_map), Jsonb(stats), repo_id)
    )


def ingest(url: str, force: bool = False, listener: Listener | None = None) -> int:
    """Synchronous ingest for the CLI. Waits if another worker is already on it."""
    repo_id, needs_run = prepare(url, force)
    if needs_run:
        run(repo_id, listener)
        return repo_id
    while (row := db.one("SELECT status, stage, error FROM repos WHERE id = %s", (repo_id,)))["status"] == "ingesting":
        time.sleep(1)
    if row["status"] == "failed":
        raise RuntimeError(row["error"])
    return repo_id


def load(repo_id: int) -> dict:
    repo = db.one("SELECT * FROM repos WHERE id = %s", (repo_id,))
    if repo is None:
        raise KeyError(repo_id)
    return repo

