import { useEffect, useState } from "react";
import { useNavigate, useParams } from "react-router-dom";
import { api, type RepoStatus } from "../api";
import { Button, ErrorNote, Layout, Spinner } from "../components/ui";

const STAGES = [
  { key: "downloading", label: "Downloading the repo" },
  { key: "filtering", label: "Skipping binaries, lockfiles, vendored code and secrets" },
  { key: "ranking", label: "Ranking files by interview value" },
  { key: "chunking", label: "Splitting code into functions and classes" },
  { key: "embedding + summarizing", label: "Indexing and summarizing every file" },
  { key: "mapping", label: "Mapping the architecture" },
  { key: "questions", label: "Writing your interview questions" },
];

function detail(key: string, r: RepoStatus): string {
  const p = r.progress ?? {};
  switch (key) {
    case "filtering":
      return p.files_seen ? `${p.files_seen} files seen, ${p.candidates ?? 0} kept` : "";
    case "ranking":
      return p.files_included ? `${p.files_included} files picked` : "";
    case "chunking":
      return p.chunks ? `${p.chunks} chunks` : "";
    case "embedding + summarizing":
      return p.chunks ? `${p.embedded ?? 0}/${p.chunks} embedded · ${p.summarized ?? 0}/${p.to_summarize ?? 0} summarized` : "";
    case "questions":
      return r.questions_ready ? `${r.questions_ready} ready` + (p.areas_total ? ` · ${p.areas_done ?? 0}/${p.areas_total} areas` : "") : "";
  }
  return "";
}

export default function RepoPage() {
  const repoId = Number(useParams().repoId);
  const navigate = useNavigate();
  const [repo, setRepo] = useState<RepoStatus | null>(null);
  const [error, setError] = useState("");
  const [starting, setStarting] = useState(false);
  const [showFiles, setShowFiles] = useState(false);

  useEffect(() => {
    let alive = true;
    let timer: number;
    const poll = async () => {
      try {
        const r = await api.repo(repoId);
        if (!alive) return;
        setRepo(r);
        const done = r.status === "failed" || (r.status === "ready" && ["done", "failed"].includes(r.questions_status));
        if (!done) timer = window.setTimeout(poll, 1000);
      } catch (e) {
        setError((e as Error).message);
      }
    };
    poll();
    return () => {
      alive = false;
      clearTimeout(timer);
    };
  }, [repoId]);

  if (error) return <Layout><ErrorNote message={error} /></Layout>;
  if (!repo) return <Layout><Spinner /></Layout>;

  const ingesting = repo.status !== "ready";
  const current = ingesting ? STAGES.findIndex((s) => s.key === repo.stage) : 6;
  const canStart = repo.status === "ready" && (repo.questions_ready >= repo.first_batch || (repo.questions_status === "done" && repo.questions_ready > 0));

  async function start() {
    setStarting(true);
    try {
      const st = await api.createSession(repoId);
      navigate(`/s/${st.session_id}`);
    } catch (e) {
      setError((e as Error).message);
    }
  }

  return (
    <Layout>
      <div className="mb-8">
        <a href={repo.github_url} target="_blank" rel="noreferrer" className="font-mono text-sm text-zinc-400 hover:text-zinc-200">
          github.com/{repo.owner}/{repo.name}
          {repo.subpath && `/${repo.subpath}`} @ {repo.commit.slice(0, 7)} ↗
        </a>
        <h1 className="mt-1 text-3xl font-semibold tracking-tight">{repo.name}</h1>
        {repo.overview && <p className="mt-3 leading-relaxed text-zinc-400">{repo.overview}</p>}
      </div>

      {repo.status === "failed" ? (
        <ErrorNote message={repo.error || "Ingestion failed."} />
      ) : (
        <ol className="mb-8 space-y-2.5">
          {STAGES.map((s, i) => {
            const qDone = s.key === "questions" && repo.questions_status === "done";
            const done = i < current || qDone;
            const active = i === current && !qDone;
            return (
              <li key={s.key} className="flex items-center gap-3">
                <span className="flex h-5 w-5 items-center justify-center">
                  {done ? <span className="text-emerald-400">✓</span> : active ? <Spinner /> : <span className="h-1.5 w-1.5 rounded-full bg-zinc-700" />}
                </span>
                <span className={done ? "text-zinc-300" : active ? "text-zinc-100" : "text-zinc-600"}>{s.label}</span>
                {(done || active) && <span className="text-sm tabular-nums text-zinc-500">{detail(s.key, repo)}</span>}
              </li>
            );
          })}
        </ol>
      )}

      {repo.status === "ready" && (
        <div className="space-y-6">
          <div className="flex flex-wrap items-center gap-4">
            <Button onClick={start} disabled={!canStart || starting} className="px-6 py-3 text-base">
              {starting && <Spinner className="border-zinc-500 border-t-zinc-950" />}
              {canStart ? "Start the interview" : "Writing your first questions…"}
            </Button>
            {canStart && repo.questions_status === "generating" && (
              <span className="text-sm text-zinc-500">The rest of the questions keep generating in the background.</span>
            )}
          </div>
          {repo.questions_status === "failed" && repo.questions_ready === 0 && (
            <ErrorNote message="Couldn't write questions for this repo. Try another one?" />
          )}
          {repo.files && (
            <div>
              <button className="text-sm text-zinc-400 hover:text-zinc-100" onClick={() => setShowFiles(!showFiles)}>
                {showFiles ? "▾" : "▸"} {repo.files.length} files included, ranked by interview value
              </button>
              {showFiles && (
                <ul className="mt-3 divide-y divide-zinc-900 rounded-xl border border-zinc-800">
                  {repo.files.map((f) => (
                    <li key={f.path} className="flex flex-wrap items-center gap-x-3 gap-y-1 px-4 py-2">
                      <span className="font-mono text-sm text-zinc-200">{f.path}</span>
                      <span className="text-xs text-zinc-500">{f.language} · {f.lines} lines</span>
                      {f.reasons.map((r) => (
                        <span key={r} className="rounded bg-zinc-800 px-1.5 py-0.5 text-[11px] text-zinc-400">{r}</span>
                      ))}
                    </li>
                  ))}
                </ul>
              )}
            </div>
          )}
        </div>
      )}
    </Layout>
  );
}
