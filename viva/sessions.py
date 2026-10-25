"""Interview sessions: picking the next question, recording attempts and summarizing.

A session is five questions with mixed types and areas. Difficulty adapts to how the last answer went,
and questions the user scored poorly on before come back first.
"""

import statistics
import uuid

from psycopg.types.json import Jsonb

from viva import db

CLI_USER = uuid.uuid5(uuid.NAMESPACE_URL, "viva-cli")
SESSION_SIZE = 5
MISSED_BELOW = 50  # a question's latest score under this brings it back in the next session
UP_AT, DOWN_BELOW = 75, 45  # adaptive difficulty thresholds
MAX_FOLLOW_UPS = 2


def ensure_user(user_id: uuid.UUID | str) -> str:
    db.execute("INSERT INTO users (id) VALUES (%s) ON CONFLICT DO NOTHING", (str(user_id),))
    return str(user_id)


def start(user_id: str, repo_id: int, size: int = SESSION_SIZE) -> dict:
    return db.one(
        "INSERT INTO sessions (user_id, repo_id, size) VALUES (%s, %s, %s) RETURNING *", (user_id, repo_id, size)
    )


def latest_scores(user_id: str, repo_id: int) -> dict[int, int]:
    """Each question's most recent score for this user (a follow-up's score supersedes its parent's)."""
    rows = db.query(
        """SELECT DISTINCT ON (a.question_id) a.question_id, a.score
           FROM attempts a JOIN sessions s ON s.id = a.session_id
           WHERE s.user_id = %s AND s.repo_id = %s AND a.score IS NOT NULL
           ORDER BY a.question_id, a.id DESC""",
        (user_id, repo_id),
    )
    return {r["question_id"]: r["score"] for r in rows}


def chains(session_id: int) -> list[dict]:
    """The session's questions in order, each with its first attempt and its latest (final) attempt."""
    return db.query(
        """SELECT m.id AS attempt_id, m.question_id, m.verdict AS first_verdict, m.score AS first_score,
                  f.verdict, f.score, q.type, q.difficulty, q.text, q.area_id, ar.name AS area,
                  (SELECT count(*) FROM attempts c WHERE c.parent_id = m.id) AS follow_ups,
                  (SELECT COALESCE(SUM((c.grading->>'contradictions')::int), 0) FROM attempts c
                    WHERE c.id = m.id OR c.parent_id = m.id) AS contradictions
           FROM attempts m
           JOIN questions q ON q.id = m.question_id
           JOIN areas ar ON ar.id = q.area_id
           JOIN LATERAL (SELECT verdict, score FROM attempts c WHERE c.id = m.id OR c.parent_id = m.id
                         ORDER BY c.id DESC LIMIT 1) f ON true
           WHERE m.session_id = %s AND m.parent_id IS NULL
           ORDER BY m.id""",
        (session_id,),
    )


def next_question(session: dict) -> dict | None:
    asked = chains(session["id"])
    if len(asked) >= session["size"]:
        return None
    target = 2
    for c in asked:  # walk the session to find the current target difficulty
        if c["score"] is not None:
            target += 1 if c["score"] >= UP_AT else -1 if c["score"] < DOWN_BELOW else 0
            target = max(1, min(3, target))
    asked_ids = {c["question_id"] for c in asked}
    used_areas = {c["area_id"] for c in asked}
    type_counts: dict[str, int] = {}
    for c in asked:
        type_counts[c["type"]] = type_counts.get(c["type"], 0) + 1
    latest = latest_scores(session["user_id"], session["repo_id"])
    candidates = db.query(
        """SELECT q.*, a.position AS area_position, a.name AS area FROM questions q JOIN areas a ON a.id = q.area_id
           WHERE q.repo_id = %s""",
        (session["repo_id"],),
    )

    def priority(q: dict):
        last = latest.get(q["id"])
        tier = 0 if last is not None and last < MISSED_BELOW else 1 if last is None else 2
        return (tier, q["area_id"] in used_areas, abs(q["difficulty"] - target), type_counts.get(q["type"], 0),
                q["area_position"], q["id"])

    remaining = [q for q in candidates if q["id"] not in asked_ids]
    return min(remaining, key=priority) if remaining else None


def record(session_id: int, question_id: int, prompt: str, answer: str, result: dict | None,
           parent_id: int | None = None, seconds: float | None = None, verdict: str | None = None,
           score: int | None = None) -> dict:
    """Store an attempt. result is the grader output; pass verdict/score instead for skips and carries."""
    return db.one(
        """INSERT INTO attempts (session_id, question_id, parent_id, prompt, answer, verdict, score, grading, seconds)
           VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s) RETURNING *""",
        (session_id, question_id, parent_id, prompt, answer,
         verdict or result["verdict"], score if result is None else result["score"],
         Jsonb(result) if result is not None else None, round(seconds) if seconds is not None else None),
    )


def summary(session_id: int) -> dict:
    rows = chains(session_id)
    answered = [r for r in rows if r["verdict"] != "skipped" and r["score"] is not None]
    by_area: dict[str, list[int]] = {}
    for r in answered:
        by_area.setdefault(r["area"], []).append(r["score"])
    area_scores = {a: round(statistics.mean(s)) for a, s in by_area.items()}
    ranked = sorted(area_scores, key=lambda a: -area_scores[a])
    return {
        "session_id": session_id,
        "questions": rows,
        "answered": len(answered),
        "skipped": len(rows) - len(answered),
        "score": round(statistics.mean(r["score"] for r in answered)) if answered else 0,
        "contradictions": sum(r["contradictions"] for r in rows),
        "area_scores": area_scores,
        "strongest": ranked[0] if len(ranked) > 1 else None,
        "weakest": ranked[-1] if len(ranked) > 1 else None,
    }


def finish(session_id: int) -> dict:
    db.execute("UPDATE sessions SET status = 'finished', finished_at = now() WHERE id = %s AND status = 'active'", (session_id,))
    return summary(session_id)
