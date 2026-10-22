"""Command line: python -m viva <command> ..."""

import argparse
import sys

from rich.console import Console
from rich.markup import escape
from rich.panel import Panel
from rich.table import Table

from viva import db, github, ingest, llm, retrieval

console = Console()


def _link(repo: dict, path: str, start: int | None = None, end: int | None = None) -> str:
    return github.blob_url(repo["owner"], repo["name"], repo["commit_sha"], path, start, end)


def cmd_ingest(args) -> None:
    with console.status("Resolving repo…") as status:

        def listener(stage: str, counts: dict) -> None:
            if stage:
                console.log(f"[bold cyan]{stage}[/] {' '.join(f'{k}={v}' for k, v in counts.items())}")
            status.update(f"{stage or 'working'} {' '.join(f'{k}={v}' for k, v in counts.items())}")

        repo_id = ingest.ingest(args.url, force=args.force, listener=listener)
    repo = ingest.load(repo_id)
    print_report(repo)


def print_report(repo: dict) -> None:
    stats = repo["stats"] or {}
    where = f"{repo['owner']}/{repo['name']}" + (f" [dim]/{repo['subpath']}[/]" if repo["subpath"] else "")
    console.print(
        Panel.fit(
            f"[bold]{where}[/] @ {repo['commit_sha'][:10]}  (repo id [bold]{repo['id']}[/])\n"
            f"{stats.get('files_seen', '?')} files seen → {stats.get('candidates', '?')} passed filters → "
            f"[bold]{stats.get('included', '?')} included[/], {stats.get('chunks', '?')} chunks, "
            f"{stats.get('lines_included', '?')} lines\n"
            f"languages: {', '.join(f'{k} {v}' for k, v in (stats.get('languages') or {}).items())}\n"
            f"took {stats.get('seconds', '?')}s, cost ${stats.get('cost_usd', '?')}",
            title="Ingested",
        )
    )
    print_files(repo, show_all=False)
    if stats.get("skipped"):
        console.print("[bold]Skipped:[/] " + ", ".join(f"{v} {k}" for k, v in stats["skipped"].items()))
    print_map(repo)


def print_files(repo: dict, show_all: bool) -> None:
    rows = db.query(
        """SELECT path, language, line_count, score, reasons, included, summary FROM files
           WHERE repo_id = %s AND (included OR %s) ORDER BY included DESC, score DESC, path""",
        (repo["id"], show_all),
    )
    t = Table(title="Files (ranked by interview value)", show_lines=False)
    for col in ("#", "path", "lang", "lines", "score", "why"):
        t.add_column(col, overflow="fold")
    for i, r in enumerate(rows, 1):
        style = None if r["included"] else "dim"
        t.add_row(str(i), escape(r["path"]), r["language"], str(r["line_count"]), f"{r['score']:.1f}", ", ".join(r["reasons"]), style=style)
    console.print(t)


def print_map(repo: dict) -> None:
    ov = (repo["repo_map"] or {}).get("overview")
    if not ov:
        return
    body = f"{escape(ov['summary'])}\n\n[bold]Architecture[/]\n{escape(ov['architecture'])}\n\n[bold]Data flow[/]\n{escape(ov['data_flow'])}"
    if ov.get("deployment"):
        body += f"\n\n[bold]Deployment[/]\n{escape(ov['deployment'])}"
    body += f"\n\n[bold]Stack[/] {escape(', '.join(ov['tech_stack']))}\n\n[bold]Components[/]"
    for c in ov["components"]:
        body += f"\n• [bold]{escape(c['name'])}[/]: {escape(c['description'])} [dim]({escape(', '.join(c['files']))})[/]"
    console.print(Panel(body, title="Repo map"))


def cmd_files(args) -> None:
    print_files(ingest.load(args.repo_id), show_all=args.all)


def cmd_map(args) -> None:
    repo = ingest.load(args.repo_id)
    for f in db.query("SELECT path, summary, symbols FROM files WHERE repo_id = %s AND included ORDER BY score DESC", (repo["id"],)):
        console.print(f"[bold]{escape(f['path'])}[/] [dim]{escape(', '.join(f['symbols'][:6]))}[/]\n  {escape(f['summary'] or '(no summary)')}")
    print_map(repo)


def cmd_chunks(args) -> None:
    repo = ingest.load(args.repo_id)
    rows = db.query(
        "SELECT kind, symbol, start_line, end_line, content FROM chunks WHERE repo_id = %s AND path = %s ORDER BY start_line",
        (repo["id"], args.path),
    )
    if not rows:
        console.print("No chunks for that path. Use `files` to see included paths.")
    for r in rows:
        console.print(f"[bold]L{r['start_line']}-{r['end_line']}[/] {r['kind']} [cyan]{escape(r['symbol'] or '')}[/]  {_link(repo, args.path, r['start_line'], r['end_line'])}")
        if args.show:
            console.print(escape(r["content"]), highlight=False)


def cmd_search(args) -> None:
    repo = ingest.load(args.repo_id)
    for r in retrieval.search(repo["id"], " ".join(args.query), k=args.k):
        console.print(f"[bold]{escape(r['path'])}[/] L{r['start_line']}-{r['end_line']} {r['kind']} [cyan]{escape(r['symbol'] or '')}[/] score={r['score']}")
        console.print(f"  {_link(repo, r['path'], r['start_line'], r['end_line'])}")


def cmd_verify(args) -> None:
    """Re-download every chunked file from GitHub and check each chunk's line range matches."""
    import httpx

    repo = ingest.load(args.repo_id)
    paths = [r["path"] for r in db.query("SELECT DISTINCT path FROM chunks WHERE repo_id = %s", (repo["id"],))]
    checked = bad = 0
    with httpx.Client(timeout=30) as http, console.status("Checking against GitHub…"):
        for path in paths:
            url = f"https://raw.githubusercontent.com/{repo['owner']}/{repo['name']}/{repo['commit_sha']}/{path}"
            lines = http.get(url).text.replace("\r\n", "\n").split("\n")
            for c in db.query(
                "SELECT start_line, end_line, content FROM chunks WHERE repo_id = %s AND path = %s", (repo["id"], path)
            ):
                checked += 1
                if "\n".join(lines[c["start_line"] - 1:c["end_line"]]) != c["content"]:
                    bad += 1
                    console.print(f"[red]MISMATCH[/] {escape(path)} L{c['start_line']}-{c['end_line']}")
    color = "green" if bad == 0 else "red"
    console.print(f"[{color}]{checked} chunks in {len(paths)} files checked against GitHub: {bad} mismatches[/]")


def cmd_repos(args) -> None:
    t = Table(title="Repos")
    for col in ("id", "repo", "commit", "status", "stage", "updated"):
        t.add_column(col)
    for r in db.query("SELECT * FROM repos ORDER BY updated_at DESC LIMIT 30"):
        name = f"{r['owner']}/{r['name']}" + (f"/{r['subpath']}" if r["subpath"] else "")
        t.add_row(str(r["id"]), name, r["commit_sha"][:10], r["status"], r["stage"] or "", r["updated_at"].strftime("%Y-%m-%d %H:%M"))
    console.print(t)


def main() -> None:
    p = argparse.ArgumentParser(prog="python -m viva")
    sub = p.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("ingest", help="ingest a public GitHub repo")
    s.add_argument("url")
    s.add_argument("--force", action="store_true", help="re-ingest even if this commit is cached")
    s.set_defaults(fn=cmd_ingest)
    s = sub.add_parser("files", help="list a repo's files with ranking")
    s.add_argument("repo_id", type=int)
    s.add_argument("--all", action="store_true", help="include files that were cut by the cap")
    s.set_defaults(fn=cmd_files)
    s = sub.add_parser("map", help="show file summaries and the architecture overview")
    s.add_argument("repo_id", type=int)
    s.set_defaults(fn=cmd_map)
    s = sub.add_parser("chunks", help="show the chunks of one file with GitHub line links")
    s.add_argument("repo_id", type=int)
    s.add_argument("path")
    s.add_argument("--show", action="store_true", help="print chunk contents")
    s.set_defaults(fn=cmd_chunks)
    s = sub.add_parser("search", help="hybrid search over a repo")
    s.add_argument("repo_id", type=int)
    s.add_argument("query", nargs="+")
    s.add_argument("-k", type=int, default=6)
    s.set_defaults(fn=cmd_search)
    s = sub.add_parser("verify", help="check every chunk's line numbers against GitHub")
    s.add_argument("repo_id", type=int)
    s.set_defaults(fn=cmd_verify)
    s = sub.add_parser("repos", help="list ingested repos")
    s.set_defaults(fn=cmd_repos)
    args = p.parse_args()
    try:
        args.fn(args)
    except (github.BadRepoURL, github.GitHubError, llm.LLMError, RuntimeError, KeyError) as e:
        console.print(f"[bold red]Error:[/] {escape(str(e))}")
        sys.exit(1)


if __name__ == "__main__":
    main()
