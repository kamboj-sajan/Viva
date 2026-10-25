"""Grader eval: hand-written answers on a pinned commit, each with the verdicts it should get.

Besides pass/fail per case, it counts false contradictions (a "contradicts your code" on an answer
that isn't wrong), the worst possible bug. Every run is appended to evals/history.jsonl.
"""

import datetime
import json
import pathlib
import time
from concurrent.futures import ThreadPoolExecutor

from viva import citations, config, grading, ingest, usage

ROOT = pathlib.Path(__file__).resolve().parents[1] / "evals"
CASES = ROOT / "grader_cases.json"
HISTORY = ROOT / "history.jsonl"


def run(only: str | None = None, note: str = "") -> tuple[dict, list[dict]]:
    spec = json.loads(CASES.read_text())
    repo_id = ingest.ingest(spec["repo"])
    repo = ingest.load(repo_id)
    files = citations.load_files(repo_id)
    for qid, q in spec["questions"].items():  # the hand-written keys must pass the same validator
        for kp in q["answer_key"]:
            for ev in kp["evidence"]:
                if not (r := citations.check(ev, files)).ok:
                    raise RuntimeError(f"Eval answer key '{qid}' has a bad citation {ev}: {r.reason}")
    cases = [c for c in spec["cases"] if not only or c["id"] == only]
    spent_before = usage.spent_today()

    def one(case: dict) -> dict:
        q = spec["questions"][case["question"]]
        started = time.time()
        exchanges = [(q["text"], case["answer"])] + [(f["prompt"], f["answer"]) for f in case.get("follow_ups", [])]
        res = grading.grade(repo, q, exchanges, files)
        wrong_expected = "contradicts_code" in case["expect"]
        return {
            "id": case["id"],
            "expect": case["expect"],
            "verdict": res["verdict"],
            "model_verdict": res["model_verdict"],
            "score": res["score"],
            "contradictions": res["contradictions"],
            "downgraded": res["downgraded_claims"],
            "passed": res["verdict"] in case["expect"],
            "false_contradiction": res["contradictions"] > 0 and not wrong_expected,
            "seconds": round(time.time() - started, 1),
            "points": "".join({"hit": "✓", "partial": "◐", "missed": "○", "wrong": "✗"}[p["status"]] for p in res["key_points"]),
            "feedback": res["feedback"],
        }

    with ThreadPoolExecutor(max_workers=4) as pool:
        results = list(pool.map(one, cases))
    summary = {
        "at": datetime.datetime.now().isoformat(timespec="seconds"),
        "model": config.LLM_MODEL_STRONG,
        "effort": config.LLM_EFFORT_GRADE,
        "passed": sum(r["passed"] for r in results),
        "total": len(results),
        "false_contradictions": sum(r["false_contradiction"] for r in results),
        "avg_seconds": round(sum(r["seconds"] for r in results) / max(len(results), 1), 1),
        "cost_usd": round(usage.spent_today() - spent_before, 4),
        "failed": [r["id"] for r in results if not r["passed"]],
        "note": note,
    }
    if not only:
        with HISTORY.open("a") as f:
            f.write(json.dumps(summary) + "\n")
    return summary, results
