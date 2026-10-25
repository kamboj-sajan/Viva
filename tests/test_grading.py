"""Grounding rules and the scoring rubric, without calling a model."""

from viva.citations import file_lines
from viva.grading import Cite, Claim, Grade, PointGrade, compute_score, finalize, is_dont_know, reconcile

SRC = "def fetch(url):\n    for attempt in range(3):\n        try:\n            return get(url)\n        except Timeout:\n            sleep(2 ** attempt)\n    raise RuntimeError('gave up')\n"
FILES = {"net.py": file_lines(SRC)}
QUESTION = {
    "open": False,
    "follow_ups": ["What happens after the third timeout?"],
    "answer_key": [
        {"point": "Retries three times", "importance": "must", "evidence": [{"path": "net.py", "start_line": 2, "end_line": 2, "quote": "for attempt in range(3):"}]},
        {"point": "Backs off exponentially", "importance": "must", "evidence": [{"path": "net.py", "start_line": 6, "end_line": 6, "quote": "sleep(2 ** attempt)"}]},
        {"point": "Gives up with an error", "importance": "nice", "evidence": [{"path": "net.py", "start_line": 7, "end_line": 7, "quote": "raise RuntimeError('gave up')"}]},
    ],
}
ANSWER = "It retries forever with a fixed one second delay."


def raw(claims, points, verdict="contradicts_code", rq=1):
    return Grade(
        claims=claims, key_points=points, verdict=verdict, reasoning_quality=rq, feedback="fb",
        follow_up="Why do you think it retries forever?", model_answer="ma [1]",
        model_answer_citations=[Cite(path="net.py", start_line=2, end_line=2, quote="for attempt in range(3):")],
    )


def claim(status, quote, start=2, end=2, words="retries forever"):
    return Claim(claim="c", words=words, status=status, explanation="e",
                 evidence=[Cite(path="net.py", start_line=start, end_line=end, quote=quote)])


def test_contradiction_with_valid_citation_stands():
    r = finalize(QUESTION, raw([claim("contradicted", "for attempt in range(3):")],
                               [PointGrade(point=1, status="wrong", note=""), PointGrade(point=2, status="missed", note="")]),
                 ANSWER, FILES)
    assert r["verdict"] == "contradicts_code" and r["contradictions"] == 1
    assert r["key_points"][0]["status"] == "wrong"
    assert r["claims"][0]["words"] == "retries forever"


def test_contradiction_with_invented_citation_is_downgraded():
    r = finalize(QUESTION, raw([claim("contradicted", "while True: retry()")],
                               [PointGrade(point=1, status="wrong", note="")]), ANSWER, FILES)
    assert r["claims"][0]["status"] == "unverifiable" and r["claims"][0]["evidence"] == []
    assert r["contradictions"] == 0 and r["downgraded_claims"] == 1
    assert r["verdict"] != "contradicts_code"
    assert r["key_points"][0]["status"] == "missed"  # "wrong" needs a surviving contradiction


def test_supported_claim_without_evidence_becomes_unverifiable():
    c = Claim(claim="c", words="retries forever", status="supported", explanation="e", evidence=[])
    r = finalize(QUESTION, raw([c], [], verdict="partial"), ANSWER, FILES)
    assert r["claims"][0]["status"] == "unverifiable"


def test_claims_the_candidate_never_made_are_dropped():
    # e.g. the grader restating an answer-key point as if the candidate had said it
    r = finalize(QUESTION, raw([claim("supported", "for attempt in range(3):", words="retries exactly three times")],
                               [], "vague"), ANSWER, FILES)
    assert r["claims"] == [] and r["unsaid_claims"] == 1


def test_lightly_cleaned_words_still_count_as_said():
    r = finalize(QUESTION, raw([claim("contradicted", "for attempt in range(3):", words="It retries forever, with a fixed delay")],
                               [], "contradicts_code"), ANSWER, FILES)
    assert r["contradictions"] == 1


def test_missing_key_point_grades_default_to_missed():
    r = finalize(QUESTION, raw([], [PointGrade(point=2, status="hit", note="")], "partial"), ANSWER, FILES)
    assert [p["status"] for p in r["key_points"]] == ["missed", "hit", "missed"]


def test_follow_up_naming_a_file_falls_back_to_stored_one():
    g = raw([], [PointGrade(point=1, status="hit", note="")], "partial")
    g.follow_up = "What does net.py do on timeout?"
    assert finalize(QUESTION, g, ANSWER, FILES)["follow_up"] == "What happens after the third timeout?"


def test_strong_has_no_follow_up():
    pts = [PointGrade(point=i, status="hit", note="") for i in (1, 2, 3)]
    r = finalize(QUESTION, raw([], pts, "strong"), ANSWER, FILES)
    assert r["verdict"] == "strong" and r["score"] == 100 and r["follow_up"] == ""


def points(*statuses, importance=("must", "must", "nice")):
    return [{"importance": i, "status": s} for i, s in zip(importance, statuses)]


def test_score_rubric():
    assert compute_score(points("hit", "hit", "hit"), 0, False, 0) == 100
    assert compute_score(points("hit", "partial", "missed"), 0, False, 0) == 60
    missing = compute_score(points("hit", "missed", "hit"), 0, False, 0)
    wrong = compute_score(points("hit", "wrong", "hit"), 1, False, 0)
    assert wrong < missing  # a contradicted claim costs more than leaving it out
    assert compute_score(points("wrong", "wrong", "wrong"), 3, False, 0) == 0


def test_open_question_score_rewards_reasoning():
    assert compute_score(points("hit", "missed", "missed"), 0, True, 4) == 82
    assert compute_score(points("hit", "missed", "missed"), 0, True, 1) < 40


def test_reconcile():
    assert reconcile("strong", 90, 1) == ("contradicts_code", 55)
    assert reconcile("contradicts_code", 50, 0) == ("partial", 50)
    assert reconcile("contradicts_code", 20, 0) == ("wrong", 20)
    assert reconcile("strong", 60, 0) == ("partial", 60)
    assert reconcile("partial", 90, 0) == ("strong", 90)
    assert reconcile("vague", 60, 0) == ("vague", 40)
    assert reconcile("dont_know", 30, 0) == ("dont_know", 0)


def test_is_dont_know():
    for a in ["I don't know", "idk", "Honestly, I don't remember.", "no idea", "Um, I don't really know this", "", "pass"]:
        assert is_dont_know(a), a
    for a in ["I'm not sure but I think it retries three times", "It doesn't know the length upfront, so it streams"]:
        assert not is_dont_know(a), a


def test_follow_up_only_counts_contradictions_in_latest_answer():
    first = "It retries forever with a fixed one second delay."
    latest = "Actually it stops after three attempts and backs off exponentially."
    g = raw([claim("contradicted", "for attempt in range(3):", words="It retries forever")],
            [PointGrade(point=1, status="hit", note=""), PointGrade(point=2, status="hit", note="")], "contradicts_code", rq=3)
    r = finalize(QUESTION, g, first + "\n" + latest, FILES, latest=latest)
    assert r["contradictions"] == 0 and r["verdict"] != "contradicts_code"
    g2 = raw([claim("contradicted", "for attempt in range(3):", words="stops after three attempts")], [], "contradicts_code")
    assert finalize(QUESTION, g2, first + "\n" + latest, FILES, latest=latest)["contradictions"] == 1


def test_readiness_is_difficulty_weighted():
    from viva.sessions import readiness

    assert readiness({1: 100, 2: 40}, {1: 1, 2: 3}) == 55  # (100*1 + 40*3) / 4
    assert readiness({}, {1: 2}) is None


def test_rate_limiter():
    import pytest
    from fastapi import HTTPException

    from viva.app import Limiter

    lim = Limiter()
    for _ in range(3):
        lim.check("ip:1", 3)
    with pytest.raises(HTTPException) as e:
        lim.check("ip:1", 3)
    assert e.value.status_code == 429
    lim.check("ip:2", 3)  # other clients are unaffected
