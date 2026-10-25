"""Web app: a JSON API under /api plus the built React app, served from one process.

Users are anonymous, identified by a cookie. Ingestion and question generation run in background
threads; the browser polls. Interview sessions are driven by the server: the client only ever sends
"here is my answer" and asks for the next step, so follow-ups can't be skipped around or forged.
"""

import contextlib
import copy
import functools
import logging
import pathlib
import threading
import time
import uuid
from collections import defaultdict, deque

from fastapi import Body, Depends, FastAPI, HTTPException, Request, Response
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from viva import citations, config, db, github, grading, ingest, llm, questions, sessions, usage

log = logging.getLogger("viva")
WEB = pathlib.Path(__file__).resolve().parents[1] / "web" / "dist"
COOKIE = "viva_uid"


@contextlib.asynccontextmanager
async def lifespan(_: FastAPI):
    db.pool()  # applies the schema
    yield


app = FastAPI(title="Viva", docs_url=None, redoc_url=None, lifespan=lifespan)


# --- users, limits, errors ----------------------------------------------------------------------


def current_user(request: Request, response: Response) -> str:
    uid = request.cookies.get(COOKIE, "")
    try:
        uid = str(uuid.UUID(uid))
    except ValueError:
        uid = str(uuid.uuid4())
        response.set_cookie(COOKIE, uid, max_age=365 * 86400, httponly=True, samesite="lax",
                            secure=request.url.scheme == "https" or request.headers.get("x-forwarded-proto") == "https")
    sessions.ensure_user(uid)
    return uid


def client_ip(request: Request) -> str:
    forwarded = request.headers.get("x-forwarded-for")
    return forwarded.split(",")[0].strip() if forwarded else (request.client.host if request.client else "?")


class Limiter:
    """Sliding-window limits per key, in memory (one instance serves the app)."""

    def __init__(self) -> None:
        self.hits: dict[str, deque] = defaultdict(deque)
        self.lock = threading.Lock()

    def check(self, key: str, limit: int, window: int = 3600) -> None:
        now = time.time()
        with self.lock:
            hits = self.hits[key]
            while hits and hits[0] <= now - window:
                hits.popleft()
            if len(hits) >= limit:
                minutes = int((hits[0] + window - now) / 60) + 1
                raise HTTPException(429, f"You're going a bit fast for the public demo. Try again in {minutes} minutes.")
            hits.append(now)


limiter = Limiter()


def budget_guard() -> None:
    if usage.spent_today() >= config.DAILY_BUDGET_USD:
        raise HTTPException(503, "Viva has used up today's demo budget. Please come back tomorrow, or browse a sample repo's report.")


@app.exception_handler(github.BadRepoURL)
@app.exception_handler(github.GitHubError)
def _bad_repo(_: Request, e: Exception) -> JSONResponse:
    return JSONResponse({"detail": str(e)}, status_code=400)


@app.exception_handler(llm.BudgetExceeded)
def _budget(_: Request, e: Exception) -> JSONResponse:
    return JSONResponse({"detail": str(e)}, status_code=503)


@app.exception_handler(llm.LLMError)
def _llm(_: Request, e: Exception) -> JSONResponse:
    log.warning("LLM error: %s", e)
    return JSONResponse({"detail": "The grader had a hiccup. Please try again."}, status_code=502)


# --- background pipeline ------------------------------------------------------------------------

_jobs: set[int] = set()
_jobs_lock = threading.Lock()


def start_pipeline(repo_id: int, needs_ingest: bool) -> None:
    with _jobs_lock:
        if repo_id in _jobs:
            return
        _jobs.add(repo_id)

    def work() -> None:
        try:
            if needs_ingest:
                ingest.run(repo_id)
            questions.generate(repo_id)
        except Exception:
            log.exception("pipeline failed for repo %s", repo_id)
        finally:
            with _jobs_lock:
                _jobs.discard(repo_id)

    threading.Thread(target=work, daemon=True, name=f"pipeline-{repo_id}").start()


@functools.lru_cache(maxsize=16)
def repo_files(repo_id: int) -> dict[str, list[str]]:
    return citations.load_files(repo_id)


def questions_ready(repo_id: int) -> int:
    return db.one("SELECT count(*) AS n FROM questions WHERE repo_id = %s", (repo_id,))["n"]


def repo_meta(repo: dict) -> dict:
    return {"id": repo["id"], "owner": repo["owner"], "name": repo["name"], "subpath": repo["subpath"],
            "commit": repo["commit_sha"], "github_url": f"https://github.com/{repo['owner']}/{repo['name']}"}


# --- repos --------------------------------------------------------------------------------------


@app.get("/api/config")
def get_config() -> dict:
    samples = []
    for url in config.SAMPLE_REPOS:
        u = github.parse_repo_url(url)
        sha = u.rest[0] if u.rest else None
        row = db.one(
            "SELECT * FROM repos WHERE owner = %s AND name = %s AND commit_sha = %s AND status = 'ready' ORDER BY id LIMIT 1",
            (u.owner.lower(), u.name.lower(), sha),
        ) if sha else None
        summary = ((row or {}).get("repo_map") or {}).get("overview", {}).get("summary", "")
        samples.append({"url": url, "owner": u.owner, "name": u.name, "repo_id": row["id"] if row else None,
                        "ready": bool(row) and questions_ready(row["id"]) >= questions.FIRST_BATCH,
                        "summary": summary.split(". ")[0].rstrip(".") + "." if summary else ""})
    return {"samples": samples, "budget_exhausted": usage.spent_today() >= config.DAILY_BUDGET_USD}


@app.post("/api/repos")
def create_repo(request: Request, body: dict = Body(...), uid: str = Depends(current_user)) -> dict:
    budget_guard()
    limiter.check(f"repos:{client_ip(request)}", config.RATE_REPOS_PER_HOUR)
    repo_id, needs_run = ingest.prepare(str(body.get("url", "")))
    repo = ingest.load(repo_id)
    if needs_run or repo["questions_status"] in ("pending", "failed"):
        start_pipeline(repo_id, needs_run)
    return {"repo_id": repo_id}


@app.get("/api/repos/{repo_id}")
def get_repo(repo_id: int) -> dict:
    try:
        repo = ingest.load(repo_id)
    except KeyError:
        raise HTTPException(404, "No such repo.")
    out = repo_meta(repo) | {
        "status": repo["status"], "stage": repo["stage"], "progress": repo["progress"], "error": repo["error"],
        "stats": repo["stats"], "questions_status": repo["questions_status"], "questions_ready": questions_ready(repo_id),
        "first_batch": questions.FIRST_BATCH,
    }
    if repo["status"] == "ready":
        out["files"] = db.query(
            """SELECT path, language, line_count AS lines, reasons, infra FROM files
               WHERE repo_id = %s AND included ORDER BY score DESC, path""",
            (repo_id,),
        )
        out["overview"] = (repo["repo_map"] or {}).get("overview", {}).get("summary", "")
        out["areas"] = db.query("SELECT name, kind, status FROM areas WHERE repo_id = %s ORDER BY position", (repo_id,))
    return out


# --- sessions -----------------------------------------------------------------------------------


def snippet(repo: dict, files: dict[str, list[str]], ev: dict, context: int = 2, max_lines: int = 12) -> dict:
    lines = files.get(ev["path"], [])
    start, end = ev["start_line"], ev["end_line"]
    s, e = max(1, start - context), min(len(lines), min(end, start + max_lines - 1) + context)
    return ev | {"url": github.blob_url(repo["owner"], repo["name"], repo["commit_sha"], ev["path"], start, end),
                 "snippet": {"start": s, "lines": lines[s - 1:e]}}


def enrich(repo: dict, result: dict | None) -> dict | None:
    """Attach code snippets and GitHub links to every citation in a grading result."""
    if result is None:
        return None
    files = repo_files(repo["id"])
    out = copy.deepcopy(result)
    for c in out["claims"]:
        c["evidence"] = [snippet(repo, files, ev) for ev in c["evidence"]]
    for p in out["key_points"]:
        p["evidence"] = [snippet(repo, files, ev) for ev in p["evidence"]]
    out["model_answer_citations"] = [snippet(repo, files, ev) for ev in out["model_answer_citations"]]
    return out


def attempt_view(repo: dict, a: dict) -> dict:
    return {"id": a["id"], "prompt": a["prompt"], "answer": a["answer"], "verdict": a["verdict"], "score": a["score"],
            "seconds": a["seconds"], "follow_up": a["parent_id"] is not None, "grading": enrich(repo, a["grading"])}


def own_session(session_id: int, uid: str) -> dict:
    s = db.one("SELECT * FROM sessions WHERE id = %s", (session_id,))
    if s is None or str(s["user_id"]) != uid:
        raise HTTPException(404, "No such session.")
    return s


def chain(session_id: int, question_id: int) -> list[dict]:
    return db.query("SELECT * FROM attempts WHERE session_id = %s AND question_id = %s ORDER BY id", (session_id, question_id))


def pending(attempts: list[dict]) -> tuple[str, str | None]:
    """What the current question is waiting for: "question", "follow_up" (with its prompt) or "reveal"."""
    if not attempts:
        return "question", None
    first, last = attempts[0], attempts[-1]
    g = last["grading"]
    if first["verdict"] == "skipped":
        return "skipped", None
    if (last["verdict"] in ("strong", "dont_know", "skipped") or g is None or not g.get("follow_up")
            or len(attempts) - 1 >= sessions.MAX_FOLLOW_UPS):
        return "reveal", None
    return "follow_up", g["follow_up"]


def question_view(q: dict) -> dict:
    return {"id": q["id"], "text": q["text"], "type": q["type"], "difficulty": q["difficulty"], "open": q["open"], "area": q["area"]}


def state(session: dict) -> dict:
    repo = ingest.load(session["repo_id"])
    base = {"session_id": session["id"], "size": session["size"], "repo": repo_meta(repo)}
    if session["status"] == "finished":
        return base | {"mode": "done", "summary": summary_view(session)}
    if session["current_question_id"] is None:
        return advance(session)
    q = db.one("""SELECT q.*, a.name AS area FROM questions q JOIN areas a ON a.id = q.area_id WHERE q.id = %s""",
               (session["current_question_id"],))
    attempts = chain(session["id"], q["id"])
    mode, prompt = pending(attempts)
    if mode == "skipped":
        return advance(session)
    asked = len(sessions.chains(session["id"]))
    return base | {"mode": mode, "n": asked + (0 if attempts else 1), "question": question_view(q),
                   "prompt": prompt or q["text"], "attempts": [attempt_view(repo, a) for a in attempts]}


def advance(session: dict) -> dict:
    q = sessions.next_question(session)
    if q is None:
        sessions.finish(session["id"])
    db.execute("UPDATE sessions SET current_question_id = %s WHERE id = %s", (q["id"] if q else None, session["id"]))
    return state(db.one("SELECT * FROM sessions WHERE id = %s", (session["id"],)))


def summary_view(session: dict) -> dict:
    s = sessions.summary(session["id"])
    share = db.one("SELECT share_token FROM sessions WHERE id = %s", (session["id"],))["share_token"]
    return s | {"share_token": share, "repo_id": session["repo_id"],
                "questions": [{k: r[k] for k in ("question_id", "text", "area", "type", "difficulty", "verdict", "score",
                                                 "follow_ups", "contradictions")} for r in s["questions"]]}


@app.post("/api/sessions")
def create_session(body: dict = Body(...), uid: str = Depends(current_user)) -> dict:
    repo_id = int(body.get("repo_id", 0))
    try:
        repo = ingest.load(repo_id)
    except KeyError:
        raise HTTPException(404, "No such repo.")
    if repo["status"] != "ready" or questions_ready(repo_id) == 0:
        raise HTTPException(409, "Questions for this repo aren't ready yet.")
    focus = "weak" if body.get("focus") == "weak" else None
    return state(sessions.start(uid, repo_id, focus=focus))


@app.get("/api/sessions/{session_id}")
def get_session(session_id: int, uid: str = Depends(current_user)) -> dict:
    return state(own_session(session_id, uid))


@app.post("/api/sessions/{session_id}/answer")
def answer(session_id: int, request: Request, body: dict = Body(...), uid: str = Depends(current_user)) -> dict:
    session = own_session(session_id, uid)
    current = state(session)
    if current["mode"] not in ("question", "follow_up"):
        raise HTTPException(409, "Nothing to answer right now.")
    q = db.one("SELECT * FROM questions WHERE id = %s", (current["question"]["id"],))
    repo = ingest.load(session["repo_id"])
    attempts = chain(session_id, q["id"])
    action = body.get("action", "answer")
    text = str(body.get("answer", "")).strip()[:6000]
    seconds = float(body["seconds"]) if isinstance(body.get("seconds"), (int, float)) else None
    parent = attempts[0]["id"] if attempts else None
    prompt = current["prompt"]

    if action == "skip":
        last_score = next((a["score"] for a in reversed(attempts) if a["score"] is not None), None)
        sessions.record(session_id, q["id"], prompt, "", None, parent_id=parent, seconds=seconds, verdict="skipped",
                        score=None if parent is None else last_score)
        return {"attempt": None, "state": state(own_session(session_id, uid))}
    if current["mode"] == "follow_up" and (action == "idk" or grading.is_dont_know(text)):
        last_score = next((a["score"] for a in reversed(attempts) if a["score"] is not None), 0)
        a = sessions.record(session_id, q["id"], prompt, text or "I don't know.", None, parent_id=parent,
                            seconds=seconds, verdict="dont_know", score=last_score)
        return {"attempt": attempt_view(repo, a), "state": state(own_session(session_id, uid))}
    if action == "idk":
        text = "I don't know."
    if not text:
        raise HTTPException(400, "Say something first, or press I don't know.")

    exchanges = [(a["prompt"], a["answer"]) for a in attempts if a["grading"] is not None] + [(prompt, text)]
    result = None
    if len(exchanges) == 1:  # the same answer to the same question always gets the same grade
        cached = db.one(
            """SELECT grading FROM attempts WHERE question_id = %s AND parent_id IS NULL AND answer = %s
               AND grading IS NOT NULL ORDER BY id DESC LIMIT 1""",
            (q["id"], text),
        )
        result = cached["grading"] if cached else None
    if result is None:
        if not grading.is_dont_know(text) or len(exchanges) > 1:
            budget_guard()
            limiter.check(f"grade:{client_ip(request)}", config.RATE_GRADES_PER_HOUR)
        result = grading.grade(repo, q, exchanges, repo_files(repo["id"]))
    a = sessions.record(session_id, q["id"], prompt, text, result, parent_id=parent, seconds=seconds)
    return {"attempt": attempt_view(repo, a), "state": state(own_session(session_id, uid))}


@app.post("/api/sessions/{session_id}/next")
def next_step(session_id: int, uid: str = Depends(current_user)) -> dict:
    session = own_session(session_id, uid)
    current = state(session)
    if current["mode"] in ("question", "follow_up"):
        raise HTTPException(409, "Answer, skip, or say you don't know first.")
    if current["mode"] == "done":
        return current
    return advance(session)


@app.post("/api/sessions/{session_id}/finish")
def finish(session_id: int, uid: str = Depends(current_user)) -> dict:
    session = own_session(session_id, uid)
    sessions.finish(session_id)
    return state(db.one("SELECT * FROM sessions WHERE id = %s", (session["id"],)))


# --- progress and reports -----------------------------------------------------------------------


@app.get("/api/progress/{repo_id}")
def progress(repo_id: int, uid: str = Depends(current_user)) -> dict:
    try:
        repo = ingest.load(repo_id)
    except KeyError:
        raise HTTPException(404, "No such repo.")
    return {"repo": repo_meta(repo)} | sessions.progress(uid, repo_id)


@app.get("/api/me/repos")
def my_repos(uid: str = Depends(current_user)) -> list[dict]:
    return db.query(
        """SELECT r.id, r.owner, r.name, count(DISTINCT s.id) AS sessions, max(s.created_at) AS last
           FROM sessions s JOIN repos r ON r.id = s.repo_id WHERE s.user_id = %s
           GROUP BY r.id ORDER BY last DESC LIMIT 10""",
        (uid,),
    )


@app.get("/api/report/{token}")
def report(token: str) -> dict:
    session = db.one("SELECT * FROM sessions WHERE share_token = %s", (token,))
    if session is None:
        raise HTTPException(404, "This report doesn't exist.")
    repo = ingest.load(session["repo_id"])
    view = summary_view(session)
    for item in view["questions"]:
        item["attempts"] = [attempt_view(repo, a) for a in chain(session["id"], item["question_id"])]
    return {"repo": repo_meta(repo), "summary": view, "finished_at": session["finished_at"]}


# --- the React app ------------------------------------------------------------------------------

if (WEB / "assets").exists():
    app.mount("/assets", StaticFiles(directory=WEB / "assets"), name="assets")


@app.get("/{path:path}", include_in_schema=False)
def spa(path: str):
    if path.startswith("api/"):
        raise HTTPException(404)
    file = WEB / path
    if path and file.is_file() and WEB in file.resolve().parents:
        return FileResponse(file)
    index = WEB / "index.html"
    if not index.exists():
        return JSONResponse({"detail": "Frontend not built. Run `npm run build` in web/."}, status_code=503)
    return FileResponse(index)
