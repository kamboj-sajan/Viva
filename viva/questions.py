"""Question generation: pick interview areas from the repo map, then write questions for each area
from that area's code only. Every piece of evidence is checked in code (citations.py); key points
that fail are dropped, and so are questions left without enough verified must-have points.
"""

import posixpath
import re
import threading
from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Callable, Literal

from psycopg.types.json import Jsonb
from pydantic import BaseModel, Field

from viva import citations, db, embeddings, llm, retrieval
from viva.repomap import UNTRUSTED

TYPES = ("how_it_works", "failure_modes", "design_decision", "scaling")
OPEN_TYPES = {"design_decision", "scaling"}  # the code can't prove the "why": graded on soundness
MIX = {"how_it_works": 0.40, "failure_modes": 0.25, "design_decision": 0.20, "scaling": 0.15}
AFFINITY = {  # which question types suit each kind of area, best first
    "core_flow": ["how_it_works", "failure_modes", "design_decision", "scaling"],
    "component": ["how_it_works", "design_decision", "failure_modes", "scaling"],
    "failure_handling": ["failure_modes", "how_it_works", "design_decision", "scaling"],
    "data_model": ["how_it_works", "design_decision", "scaling", "failure_modes"],
    "deployment": ["scaling", "design_decision", "how_it_works", "failure_modes"],
}
MAX_AREAS = 10
CONTEXT_CHARS = 48_000  # ~12-15K tokens of code per area
FIRST_BATCH = 5
WORKERS = 4
# Cosine similarity above which a new question counts as a repeat of one already stored. Measured on
# real output: rephrased duplicates scored 0.72-0.82, distinct-but-related questions 0.68 and below.
DUP_SIMILARITY = 0.72
_insert_lock = threading.Lock()
Listener = Callable[[str, dict], None]


class AreaSpec(BaseModel):
    name: str = Field(description="Short topic name, e.g. 'Upload processing pipeline'")
    kind: Literal["core_flow", "component", "failure_handling", "data_model", "deployment"]
    description: str = Field(description="1-2 sentences: what an interviewer should probe here")
    files: list[str] = Field(description="1-6 paths from the file list, most relevant first, copied exactly")


class AreaPlan(BaseModel):
    areas: list[AreaSpec]


class Evidence(BaseModel):
    path: str
    start_line: int
    end_line: int
    quote: str = Field(description="Exact excerpt copied from those lines, 15-120 characters")


class KeyPoint(BaseModel):
    point: str = Field(description="One sentence stating a fact about what the code does")
    importance: Literal["must", "nice"]
    evidence: list[Evidence] = Field(description="1-2 citations that prove the point")


class Question(BaseModel):
    type: Literal["how_it_works", "failure_modes", "design_decision", "scaling"]
    difficulty: Literal[1, 2, 3]
    question: str
    key_points: list[KeyPoint] = Field(description="2-5 points a strong answer covers")
    misconceptions: list[str] = Field(description="2-3 plausible but wrong answers specific to this code")
    follow_ups: list[str] = Field(description="Exactly 2 follow-up questions")


class AreaQuestions(BaseModel):
    questions: list[Question]


# --- choosing areas ---------------------------------------------------------------------------


def select_areas(repo: dict) -> list[dict]:
    files = db.query(
        "SELECT path, line_count, summary, infra FROM files WHERE repo_id = %s AND included ORDER BY score DESC",
        (repo["id"],),
    )
    ov = repo["repo_map"]["overview"]
    lo, hi = (4, 6) if len(files) < 10 else (6, MAX_AREAS)
    file_list = "\n".join(
        f"- {f['path']} ({f['line_count']} lines){' [deploy/CI]' if f['infra'] else ''}: {f['summary'] or ''}" for f in files
    )
    components = "\n".join(f"- {c['name']}: {c['description']} ({', '.join(c['files'])})" for c in ov["components"])
    prompt = (
        f"Repository: {repo['owner']}/{repo['name']}\n\n"
        f"Overview: {ov['summary']}\nArchitecture: {ov['architecture']}\nData flow: {ov['data_flow']}\n"
        f"Deployment: {ov['deployment'] or 'not evident'}\n\nComponents:\n{components}\n\nFiles:\n{file_list}\n\n"
        f"Pick {lo} to {hi} areas to interview the author about, most interview-worthy first. An area is one "
        "coherent topic a senior interviewer would drill into for a few minutes: a core flow end to end, a key "
        "component, how failures and edge cases are handled, the data model or state, or how the project is "
        "built and deployed. Prefer the author's real logic over boilerplate, generated code and tests. Include a "
        "deployment area if the repo has deployment/CI files. Keep areas distinct. For each area list the 1-6 "
        "files an interviewer would need to read, most relevant first, with paths copied exactly from the list."
    )
    plan = llm.structured(
        AreaPlan,
        system="You are a senior software engineer planning a technical interview about a candidate's project. " + UNTRUSTED,
        prompt=prompt,
        tier="strong",
        purpose="areas",
        repo_id=repo["id"],
    )
    known = {f["path"] for f in files}
    areas, seen = [], set()
    for a in plan.areas:
        paths = list(dict.fromkeys(p for p in a.files if p in known))[:6]
        if paths and a.name.lower() not in seen:
            seen.add(a.name.lower())
            areas.append({"name": a.name, "kind": a.kind, "description": a.description, "files": paths})
    areas = areas[:MAX_AREAS]
    infra = [f["path"] for f in files if f["infra"]]
    if infra and not any(a["kind"] == "deployment" for a in areas) and len(areas) < MAX_AREAS:
        areas.append({"name": "Build and deployment", "kind": "deployment",
                      "description": "How the project is built, tested in CI and deployed.", "files": infra[:4]})
    if len(areas) < 3:  # the plan was unusable; fall back to the overview's components
        areas = [
            {"name": c["name"], "kind": "component", "description": c["description"], "files": [p for p in c["files"] if p in known][:6]}
            for c in ov["components"]
            if any(p in known for p in c["files"])
        ][:MAX_AREAS]
    if not areas:
        raise llm.LLMError("Couldn't find any areas to interview about.")
    return areas


def plan_types(kinds: list[str]) -> list[list[str]]:
    """Question types per area: the first half of the areas get 3 questions, the rest 2, and the
    overall mix follows MIX, with each area getting the types that suit it first."""
    counts = [3 if i < (len(kinds) + 1) // 2 else 2 for i in range(len(kinds))]
    n = sum(counts)
    quota = {t: int(MIX[t] * n) for t in TYPES}
    for t in sorted(TYPES, key=lambda t: -(MIX[t] * n - quota[t]))[: n - sum(quota.values())]:
        quota[t] += 1  # largest remainder
    plan: list[list[str]] = [[] for _ in kinds]
    for round_ in range(3):
        for i, kind in enumerate(kinds):
            if round_ >= counts[i]:
                continue
            prefs = AFFINITY.get(kind, AFFINITY["component"])
            t = (next((t for t in prefs if quota[t] > 0 and t not in plan[i]), None)
                 or next((t for t in prefs if quota[t] > 0), None) or prefs[0])
            plan[i].append(t)
            quota[t] -= 1
    return plan


# --- writing questions --------------------------------------------------------------------------


def area_context(repo_id: int, area: dict, files: dict[str, list[str]]) -> str:
    """The area's code with line numbers: small files whole, big files as their most relevant chunks."""
    paths = [p for p in area["files"] if p in files]
    size = {p: sum(len(line) + 1 for line in files[p]) for p in paths}
    ranges: dict[str, list[tuple[int, int]]] = {}
    budget = CONTEXT_CHARS
    for p in paths:
        if size[p] <= CONTEXT_CHARS / len(paths):
            ranges[p] = [(1, len(files[p]))]
            budget -= size[p]
    big = [p for p in paths if p not in ranges]
    if big:
        per_file = max(budget // len(big), 4000)
        used: dict[str, int] = defaultdict(int)
        for ch in retrieval.search(repo_id, f"{area['name']}. {area['description']}", k=80, paths=big):
            n = len(ch["content"]) + 1
            if used[ch["path"]] + n <= per_file:
                ranges.setdefault(ch["path"], []).append((ch["start_line"], ch["end_line"]))
                used[ch["path"]] += n
        for p in big:
            ranges.setdefault(p, [(1, min(len(files[p]), 120))])

    blocks = []
    for p in paths:
        merged: list[list[int]] = []
        for s, e in sorted(ranges[p]):
            if merged and s <= merged[-1][1] + 2:
                merged[-1][1] = max(merged[-1][1], e)
            else:
                merged.append([s, e])
        body = []
        for s, e in merged:
            if body or s > 1:
                body.append("  ...")
            body.extend(f"{n:>5}| {files[p][n - 1]}" for n in range(s, e + 1))
        if merged[-1][1] < len(files[p]):
            body.append("  ...")
        blocks.append(f'<file path="{p}">\n' + "\n".join(body) + "\n</file>")
    return "\n\n".join(blocks)


TYPE_GUIDE = """Question types:
- how_it_works: how a flow or component works and how data moves through it.
- failure_modes: what happens on errors, bad input, timeouts, concurrency or edge cases, and what the code
  actually does about it, including when it does nothing.
- design_decision: why it is built this way and the trade-offs against alternatives. Open-ended.
- scaling: what breaks or must change at 10-100x the load, data or team size, or how to extend it. Open-ended."""

RULES = """Style: sound like a sharp but fair senior interviewer talking to the author, so address them as the
person who built it ("you", "your"). Ask one focused thing in under 40 words, conversationally, and don't
list what the answer should mention. Never mention file names, function, class or variable names, line
numbers, magic numbers or exact expressions; describe the behavior instead ("when an upload fails halfway
through..."). No trivia: don't ask for names, constants or exact values.

Answer key: 2-5 key points a strong answer covers.
- point: one sentence stating a fact about what the code does. Only facts provable from the code shown;
  never invent behavior (retries, caching, validation, locking...) that isn't there.
- importance: "must" for what any strong answer needs, "nice" for extra depth.
- evidence: 1-2 citations. path exactly as in the file tag; start_line/end_line: the few lines that prove
  the point; quote: a single line, or part of one, copied character for character from those lines,
  15-120 characters, never a whole block, without the line-number prefix and without "...".
- For design_decision and scaling questions, the key points are facts about the current code that any
  sound answer must be consistent with (e.g. "state lives in process memory").

misconceptions: 2-3 plausible but wrong answers a candidate might give about this code, e.g. "assumes
failed jobs are retried, but they are dropped".
follow_ups: exactly 2 follow-up questions an interviewer could ask next.
difficulty: 1 warm-up, 2 solid, 3 deep. Vary it across the questions."""


def write_questions(repo: dict, area: dict, files: dict[str, list[str]], others: list[dict]) -> tuple[list[dict], dict]:
    types = area["planned_types"]
    other_areas = "\n".join(f"- {o['name']}: {o['description']}" for o in others)
    prompt = (
        f"Project: {repo['owner']}/{repo['name']}. {repo['repo_map']['overview']['summary']}\n\n"
        f"Interview area: {area['name']}: {area['description']}\n\n"
        f"Other areas get their own questions, so don't ask about these here:\n{other_areas}\n\n"
        f"Code for this area (each line starts with its line number):\n\n{area_context(repo['id'], area, files)}\n\n"
        f"Write exactly {len(types)} interview questions about this area, one per type, in this order: "
        f"{', '.join(types)}.\n\n{TYPE_GUIDE}\n\n{RULES}"
    )
    out = llm.structured(
        AreaQuestions,
        system=(
            "You are a senior software engineer interviewing a candidate about a project they built. You write "
            "questions and answer keys grounded strictly in the code you are shown. " + UNTRUSTED
        ),
        prompt=prompt,
        tier="strong",
        purpose="questions",
        repo_id=repo["id"],
        max_tokens=24_000,
    )
    return validate(out.questions, files)


def short_quote(quote: str, limit: int = 120) -> str:
    """A verified quote cut down to a short exact excerpt: its prefix, whitespace collapsed."""
    q = citations.normalize(quote)
    if len(q) <= limit:
        return q
    cut = q.rfind(" ", 0, limit)
    return q[: cut if cut >= limit // 2 else limit]


_LINE_REF = re.compile(r"\blines?\s+\d+|\bL\d+\b", re.I)


def leaks_location(text: str, paths) -> bool:
    """Questions must not name files or line numbers."""
    if _LINE_REF.search(text):
        return True
    low = text.lower()
    for p in paths:
        base = posixpath.basename(p).lower()
        if "." in base and re.search(rf"(?<![\w.]){re.escape(base)}(?!\w)", low):
            return True
    return False


def validate(questions: list[Question], files: dict[str, list[str]]) -> tuple[list[dict], dict]:
    kept, dropped, failures = [], [], []
    counts: Counter[str] = Counter()
    reasons: Counter[str] = Counter()
    for q in questions:
        if leaks_location(q.question, files):
            dropped.append({"question": q.question, "reason": "names a file or line"})
            continue
        points = []
        for kp in q.key_points[:5]:
            evidence, seen = [], set()
            for e in kp.evidence[:2]:
                r = citations.check(e.model_dump(), files)
                key = (r.path, r.start_line, r.end_line)
                if r.ok and key not in seen:
                    seen.add(key)
                    evidence.append(r.evidence() | {"quote": short_quote(r.quote)})
                    counts["evidence_repaired"] += r.repaired
                elif not r.ok:
                    counts["evidence_dropped"] += 1
                    reasons[r.reason] += 1
                    if len(failures) < 8:
                        failures.append({"reason": r.reason, "path": r.path, "lines": [r.start_line, r.end_line], "quote": r.quote[:300]})
            if evidence:
                points.append({"point": kp.point.strip(), "importance": kp.importance, "evidence": evidence})
            else:
                counts["points_dropped"] += 1
        is_open = q.type in OPEN_TYPES
        musts = sum(p["importance"] == "must" for p in points)
        if (is_open and not points) or (not is_open and musts < 2):
            dropped.append({"question": q.question, "reason": f"{musts} verified must-have points"})
            continue
        kept.append({
            "type": q.type,
            "difficulty": q.difficulty,
            "open": is_open,
            "text": q.question.strip(),
            "answer_key": points,
            "misconceptions": [m.strip() for m in q.misconceptions[:3]],
            "follow_ups": [f.strip() for f in q.follow_ups[:2]],
        })
    stats = {"returned": len(questions), "kept": len(kept), "dropped": dropped, **counts,
             "evidence_reasons": dict(reasons), "failed_citations": failures}
    return kept, stats


# --- orchestration ------------------------------------------------------------------------------


def generate(repo_id: int, listener: Listener | None = None, regenerate: bool = False) -> None:
    """Generate (or finish generating) a repo's questions. Areas run in parallel, most important
    first, so the first few questions are ready well before the rest."""
    claimed = db.one(
        """UPDATE repos SET questions_status = 'generating', updated_at = now()
           WHERE id = %s AND status = 'ready' AND (questions_status IN ('pending', 'failed') OR %s) RETURNING *""",
        (repo_id, regenerate),
    )
    if claimed is None:
        return  # not ingested yet, already done, or another worker has it
    repo = claimed

    def progress(event: str = "", **counts) -> None:
        db.execute("UPDATE repos SET progress = progress || %s, updated_at = now() WHERE id = %s", (Jsonb(counts), repo_id))
        if listener:
            listener(event, counts)

    try:
        if regenerate:
            db.execute("DELETE FROM areas WHERE repo_id = %s", (repo_id,))
        areas = db.query("SELECT * FROM areas WHERE repo_id = %s ORDER BY position", (repo_id,))
        if not areas:
            progress("choosing areas")
            specs = select_areas(repo)
            for spec, types in zip(specs, plan_types([s["kind"] for s in specs])):
                spec["planned_types"] = types
            with db.transaction() as conn, conn.cursor() as cur:
                cur.executemany(
                    """INSERT INTO areas (repo_id, position, name, kind, description, files, planned_types)
                       VALUES (%s, %s, %s, %s, %s, %s, %s)""",
                    [(repo_id, i, s["name"], s["kind"], s["description"], s["files"], s["planned_types"]) for i, s in enumerate(specs)],
                )
            areas = db.query("SELECT * FROM areas WHERE repo_id = %s ORDER BY position", (repo_id,))

        files = citations.load_files(repo_id)
        todo = [a for a in areas if a["status"] != "done"]
        ready = db.one("SELECT count(*) AS n FROM questions WHERE repo_id = %s", (repo_id,))["n"]
        progress("writing questions", areas_total=len(areas), areas_done=len(areas) - len(todo), questions_ready=ready)
        announced = ready >= FIRST_BATCH
        with ThreadPoolExecutor(max_workers=WORKERS) as pool:
            jobs = [pool.submit(_run_area, repo, a, files, [o for o in areas if o["id"] != a["id"]]) for a in todo]
            for _ in as_completed(jobs):
                ready = db.one("SELECT count(*) AS n FROM questions WHERE repo_id = %s", (repo_id,))["n"]
                done = db.one("SELECT count(*) AS n FROM areas WHERE repo_id = %s AND status IN ('done', 'failed')", (repo_id,))["n"]
                event = ""
                if not announced and ready >= FIRST_BATCH:
                    announced, event = True, "first questions ready"
                progress(event, areas_done=done, questions_ready=ready)
        failed = db.one("SELECT count(*) AS n FROM areas WHERE repo_id = %s AND status = 'failed'", (repo_id,))["n"]
        status = "failed" if ready == 0 else "done"
        db.execute("UPDATE repos SET questions_status = %s, updated_at = now() WHERE id = %s", (status, repo_id))
        progress("questions done", questions_ready=ready, areas_failed=failed)
    except Exception as e:
        db.execute("UPDATE repos SET questions_status = 'failed', error = %s WHERE id = %s", (str(e)[:500], repo_id))
        raise


def _run_area(repo: dict, area: dict, files: dict[str, list[str]], others: list[dict]) -> None:
    db.execute("UPDATE areas SET status = 'generating', error = NULL WHERE id = %s", (area["id"],))
    try:
        kept, stats = write_questions(repo, area, files, others)
        vectors = embeddings.embed([q["text"] for q in kept], "query", repo["id"]) if kept else []
    except (llm.LLMError, embeddings.EmbeddingError) as e:
        db.execute("UPDATE areas SET status = 'failed', error = %s WHERE id = %s", (str(e)[:500], area["id"]))
        return
    # Serialized so two areas finishing together can't both add the same question. Stored questions
    # are never removed here: a session may already be using them.
    with _insert_lock, db.transaction() as conn, conn.cursor() as cur:
        cur.execute("DELETE FROM questions WHERE area_id = %s", (area["id"],))
        accepted: list[tuple[dict, list[float]]] = []
        for q, v in zip(kept, vectors):
            near = cur.execute(
                """SELECT 1 - (embedding <=> %s::vector) AS sim FROM questions
                   WHERE repo_id = %s AND vector_dims(embedding) = %s ORDER BY embedding <=> %s::vector LIMIT 1""",
                (db.vector(v), repo["id"], len(v), db.vector(v)),
            ).fetchone()
            if (near and near["sim"] >= DUP_SIMILARITY) or any(_cosine(v, w) >= DUP_SIMILARITY for _, w in accepted):
                stats["dropped"].append({"question": q["text"], "reason": "repeats another question"})
                continue
            accepted.append((q, v))
        cur.executemany(
            """INSERT INTO questions (repo_id, area_id, type, difficulty, open, text, answer_key, misconceptions, follow_ups, embedding)
               VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s::vector)""",
            [(repo["id"], area["id"], q["type"], q["difficulty"], q["open"], q["text"], Jsonb(q["answer_key"]),
              Jsonb(q["misconceptions"]), Jsonb(q["follow_ups"]), db.vector(v)) for q, v in accepted],
        )
        stats["kept"] = len(accepted)
        cur.execute("UPDATE areas SET status = 'done', stats = %s WHERE id = %s", (Jsonb(stats), area["id"]))


def _cosine(a: list[float], b: list[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b))
    return dot / ((sum(x * x for x in a) * sum(y * y for y in b)) ** 0.5 or 1.0)


def start_background(repo_id: int) -> threading.Thread:
    t = threading.Thread(target=generate, args=(repo_id,), daemon=True, name=f"questions-{repo_id}")
    t.start()
    return t
