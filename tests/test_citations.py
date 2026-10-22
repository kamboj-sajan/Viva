"""The citation validator is what stands between the model and a false accusation."""

from viva.citations import check, file_lines

SRC = """import time


def fetch(url, retries=3):
    for attempt in range(retries):
        try:
            return http_get(url)
        except TimeoutError:
            time.sleep(2 ** attempt)
    raise RuntimeError("gave up")


def parse(body):
    return json.loads(body)


def parse_again(body):
    return json.loads(body)
"""
FILES = {"src/net.py": file_lines(SRC), "README.md": ["hello"]}


def ev(start, end, quote, path="src/net.py"):
    return {"path": path, "start_line": start, "end_line": end, "quote": quote}


def test_exact_match():
    r = check(ev(8, 9, "time.sleep(2 ** attempt)"), FILES)
    assert r.ok and not r.repaired and (r.start_line, r.end_line) == (8, 9)


def test_whitespace_is_normalized():
    r = check(ev(5, 7, "for attempt in range(retries):\n try:   return http_get(url)"), FILES)
    assert r.ok and not r.repaired


def test_multiline_quote_spanning_range():
    assert check(ev(8, 10, 'except TimeoutError: time.sleep(2 ** attempt) raise RuntimeError("gave up")'), FILES).ok


def test_line_number_prefixes_are_tolerated():
    r = check(ev(9, 9, "    9|             time.sleep(2 ** attempt)"), FILES)
    assert r.ok and r.quote == "time.sleep(2 ** attempt)"


def test_ellipsis_matches_parts_in_order():
    assert check(ev(4, 10, "def fetch(url, retries=3): ... raise RuntimeError"), FILES).ok
    assert not check(ev(4, 10, 'raise RuntimeError ... def fetch(url'), FILES).ok


def test_wrong_lines_are_relocated_when_quote_is_unique():
    r = check(ev(1, 2, 'raise RuntimeError("gave up")'), FILES)
    assert r.ok and r.repaired and (r.start_line, r.end_line) == (10, 10)


def test_relocation_spans_multiple_lines():
    r = check(ev(14, 15, "except TimeoutError: time.sleep(2 ** attempt)"), FILES)
    assert r.ok and r.repaired and (r.start_line, r.end_line) == (8, 9)


def test_ambiguous_quote_is_not_relocated():
    # appears in parse() and parse_again(); the cited lines contain neither
    r = check(ev(4, 5, "return json.loads(body)"), FILES)
    assert not r.ok and "ambiguous" in r.reason


def test_ambiguous_quote_is_fine_inside_cited_lines():
    r = check(ev(17, 18, "return json.loads(body)"), FILES)
    assert r.ok and not r.repaired


def test_invented_code_fails():
    r = check(ev(4, 10, "for attempt in range(retries): backoff.retry(url)"), FILES)
    assert not r.ok and r.reason == "quote not found in cited lines"


def test_missing_file_fails():
    assert check(ev(1, 1, "import time", path="src/other.py"), FILES).reason == "file not ingested"


def test_out_of_range_with_invented_quote_fails():
    assert check(ev(90, 95, "something that is not there"), FILES).reason == "lines out of range"


def test_quote_too_short():
    assert check(ev(7, 7, "url"), FILES).reason == "quote too short"


def test_reversed_range_is_accepted():
    assert check(ev(9, 8, "time.sleep(2 ** attempt)"), FILES).ok


def test_leading_dot_slash_in_path():
    assert check(ev(1, 1, "import time", path="./src/net.py"), FILES).ok


def test_overlong_range_without_unique_quote_fails():
    lines = [f"x = {i % 3}" for i in range(200)]
    r = check({"path": "big.py", "start_line": 1, "end_line": 200, "quote": "x = 1"}, {"big.py": lines})
    assert not r.ok
