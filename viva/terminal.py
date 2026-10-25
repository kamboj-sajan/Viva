"""The interview in a terminal: ask, read a dictated answer, show the verdict with receipts."""

import time

from rich.console import Console
from rich.markup import escape
from rich.padding import Padding
from rich.panel import Panel
from rich.syntax import Syntax
from rich.table import Table

from viva import citations, db, github, grading, ingest, questions, sessions

console = Console()
VERDICT = {
    "strong": ("STRONG", "bold black on green"),
    "partial": ("PARTIAL", "bold black on yellow"),
    "vague": ("VAGUE", "bold black on yellow"),
    "wrong": ("WRONG", "bold white on red"),
    "contradicts_code": ("CONTRADICTS YOUR CODE", "bold white on red"),
    "dont_know": ("I DON'T KNOW", "bold white on blue"),
    "skipped": ("SKIPPED", "dim"),
}
CLAIM = {"supported": "[green]✓ supported[/]", "contradicted": "[bold red]✗ contradicted[/]", "unverifiable": "[dim]? unverifiable[/]"}
POINT = {"hit": "[green]✓[/]", "partial": "[yellow]◐[/]", "missed": "[dim]○[/]", "wrong": "[red]✗[/]"}
TYPE = {"how_it_works": "how it works", "failure_modes": "failure modes", "design_decision": "design decision", "scaling": "scaling"}
HELP = "[dim]Hold your Wispr key and talk. Blank line to submit · /idk · /skip · /quit[/]"


def interview(repo_id: int, size: int = sessions.SESSION_SIZE) -> None:
    repo = ingest.load(repo_id)
    wait_for_questions(repo_id)
    user = sessions.ensure_user(sessions.CLI_USER)
    session = sessions.start(user, repo_id, size)
    files = citations.load_files(repo_id)
    console.print(Panel.fit(
        f"[bold]Viva[/] is interviewing you about [bold]{repo['owner']}/{repo['name']}[/] "
        f"({size} questions, follow-ups if you're vague or wrong).", border_style="cyan"))
    n = 0
    while (q := sessions.next_question(session)) is not None:
        n += 1
        show_question(n, size, q)
        answer, seconds = read_answer()
        if answer == "/quit":
            break
        if answer == "/skip":
            sessions.record(session["id"], q["id"], q["text"], "", None, seconds=seconds, verdict="skipped")
            continue
        if answer == "/idk":
            answer = "I don't know."
        exchanges = [(q["text"], answer)]
        result = _grade(repo, q, exchanges, files)
        first = sessions.record(session["id"], q["id"], q["text"], answer, result, seconds=seconds)
        follow_ups_left = sessions.MAX_FOLLOW_UPS
        while True:
            # The answer key and model answer stay hidden while a follow-up is coming, like a real interview.
            follow = result["verdict"] not in ("strong", "dont_know") and result["follow_up"] and follow_ups_left > 0
            show_result(repo, result, files, seconds, latest=answer, reveal=not follow)
            if not follow:
                break
            follow_up = result["follow_up"]
            console.print(Panel(escape(follow_up), title="Follow-up", border_style="magenta"))
            console.print(HELP)
            answer, seconds = read_answer()
            if answer in ("/quit", "/skip") or answer == "/idk" or grading.is_dont_know(answer):
                if answer not in ("/quit", "/skip"):  # not knowing the follow-up keeps the earlier score
                    sessions.record(session["id"], q["id"], follow_up, answer, None, parent_id=first["id"],
                                    seconds=seconds, verdict="dont_know", score=result["score"])
                show_reveal(repo, result)
                break
            exchanges.append((follow_up, answer))
            result = _grade(repo, q, exchanges, files)
            sessions.record(session["id"], q["id"], follow_up, answer, result, parent_id=first["id"], seconds=seconds)
            follow_ups_left -= 1
        if answer == "/quit":
            break
    show_summary(sessions.finish(session["id"]))


def wait_for_questions(repo_id: int) -> None:
    repo = ingest.load(repo_id)
    if repo["questions_status"] in ("pending", "failed"):
        questions.start_background(repo_id)
    with console.status("Writing your first questions…") as status:
        while True:
            repo = ingest.load(repo_id)
            ready = db.one("SELECT count(*) AS n FROM questions WHERE repo_id = %s", (repo_id,))["n"]
            if ready >= questions.FIRST_BATCH or repo["questions_status"] in ("done", "failed"):
                break
            status.update(f"Writing your first questions… {ready} ready")
            time.sleep(1.5)
    if ready == 0:
        raise RuntimeError("No questions could be generated for this repo.")


def _grade(repo: dict, q: dict, exchanges: list[tuple[str, str]], files) -> dict:
    with console.status("Checking your answer against the code…"):
        return grading.grade(repo, q, exchanges, files)


def show_question(n: int, size: int, q: dict) -> None:
    console.print()
    console.rule(f"[bold]Question {n} of {size}[/]")
    dots = "●" * q["difficulty"] + "○" * (3 - q["difficulty"])
    console.print(f"[magenta]{TYPE[q['type']]}[/]  difficulty {dots}  [dim]{escape(q['area'])}[/]")
    console.print(Panel(escape(q["text"]), border_style="cyan"))
    console.print(HELP)


def read_answer() -> tuple[str, float]:
    started = time.time()
    lines: list[str] = []
    while True:
        try:
            line = input("› " if not lines else "  ")
        except EOFError:
            if not lines:
                return "/quit", time.time() - started
            break
        if not line.strip():
            if lines:
                break
            continue
        if not lines and line.strip().lower() in ("/idk", "/skip", "/quit"):
            return line.strip().lower(), time.time() - started
        lines.append(line)
    return "\n".join(lines).strip(), time.time() - started


def show_result(repo: dict, result: dict, files: dict[str, list[str]], seconds: float, latest: str = "",
                reveal: bool = True) -> None:
    label, style = VERDICT[result["verdict"]]
    console.print()
    console.print(f"[{style}] {label} [/]  [bold]{result['score']}[/]/100  [dim]answered in {seconds:.0f}s[/]")
    claims = result["claims"]
    if latest and len(claims) > 1:  # after a follow-up, show what the latest answer claimed
        recent = [c for c in claims if c["words"] and c["words"].lower() in latest.lower()]
        claims = recent or claims
    if claims:
        console.print("\n[bold]What you said[/]")
    for c in claims:
        console.print(f"  {CLAIM[c['status']]}  [italic]“{escape(c['words'] or c['claim'])}”[/]")
        console.print(f"     [dim]{escape(c['explanation'])}[/]")
        for ev in c["evidence"][:1]:
            show_code(repo, ev, files, "red" if c["status"] == "contradicted" else "green")
    console.print(Panel(escape(result["feedback"]), title="Interviewer", border_style="yellow"))
    if reveal:
        show_reveal(repo, result)
    else:
        covered = sum(p["status"] == "hit" for p in result["key_points"])
        console.print(f"[dim]Key points covered so far: {covered}/{len(result['key_points'])}[/]")


def show_reveal(repo: dict, result: dict) -> None:
    console.print("[bold]Answer key[/]")
    for p in result["key_points"]:
        tag = "must" if p["importance"] == "must" else "[dim]nice[/]"
        note = f" [dim]({escape(p['note'])})[/]" if p.get("note") and p["status"] in ("partial", "wrong") else ""
        console.print(f"  {POINT[p['status']]} {tag} {escape(p['point'])}{note}")
    if result["verdict"] != "strong" and result["model_answer"]:
        console.print("[bold]A strong answer[/]")
        console.print(f"  [dim]{escape(result['model_answer'])}[/]")
        for i, ev in enumerate(result["model_answer_citations"], 1):
            url = github.blob_url(repo["owner"], repo["name"], repo["commit_sha"], ev["path"], ev["start_line"], ev["end_line"])
            console.print(f"  [dim][{i}] {escape(ev['path'])}:{ev['start_line']}-{ev['end_line']}  {url}[/]")


def show_code(repo: dict, ev: dict, files: dict[str, list[str]], color: str) -> None:
    lines = files.get(ev["path"], [])
    start, end = ev["start_line"], ev["end_line"]
    s, e = max(1, start - 2), min(len(lines), min(end, start + 9) + 2)
    code = "\n".join(lines[s - 1:e])
    syntax = Syntax(code, Syntax.guess_lexer(ev["path"], code), line_numbers=True, start_line=s,
                    highlight_lines=set(range(start, end + 1)), theme="monokai", word_wrap=True)
    console.print(Padding(syntax, (0, 0, 0, 5)))
    url = github.blob_url(repo["owner"], repo["name"], repo["commit_sha"], ev["path"], start, end)
    console.print(f"     [{color}]{escape(ev['path'])}:{start}-{end}[/] [dim]{url}[/]")


def show_summary(summary: dict) -> None:
    console.print()
    console.rule("[bold]Session summary[/]")
    t = Table(show_header=True, header_style="bold")
    for col in ("#", "area", "type", "verdict", "score", "follow-ups"):
        t.add_column(col)
    for i, r in enumerate(summary["questions"], 1):
        label, style = VERDICT[r["verdict"]]
        t.add_row(str(i), escape(r["area"]), TYPE[r["type"]], f"[{style}] {label} [/]",
                  "—" if r["score"] is None else str(r["score"]), str(r["follow_ups"]))
    console.print(t)
    lines = [f"[bold]Score {summary['score']}/100[/] over {summary['answered']} answered"
             + (f", {summary['skipped']} skipped" if summary["skipped"] else ""),
             f"Contradictions caught: [bold]{summary['contradictions']}[/]"]
    if summary["strongest"]:
        lines.append(f"Strongest area: [green]{escape(summary['strongest'])}[/] ({summary['area_scores'][summary['strongest']]})")
        lines.append(f"Weakest area: [red]{escape(summary['weakest'])}[/] ({summary['area_scores'][summary['weakest']]})")
    console.print(Panel("\n".join(lines), border_style="cyan"))
