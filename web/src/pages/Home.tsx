import { useEffect, useState, type FormEvent } from "react";
import { Link, useNavigate } from "react-router-dom";
import { api, type Sample } from "../api";
import { Button, ErrorNote, Layout, Spinner } from "../components/ui";

const STEPS = [
  ["Reads your code", "Viva pulls the repo at its latest commit, ranks the files an interviewer would care about, and maps the architecture."],
  ["Asks like a senior engineer", "Questions on how it works, what breaks, why you built it that way, and how it scales. You answer out loud with Wispr Flow."],
  ["Grades with receipts", "Every claim you make is checked against your code. When you're wrong, it shows the exact lines that prove it."],
];

export default function Home() {
  const navigate = useNavigate();
  const [url, setUrl] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [samples, setSamples] = useState<Sample[]>([]);
  const [budgetOut, setBudgetOut] = useState(false);
  const [mine, setMine] = useState<{ id: number; owner: string; name: string; sessions: number }[]>([]);

  useEffect(() => {
    api.config().then((c) => {
      setSamples(c.samples);
      setBudgetOut(c.budget_exhausted);
    });
    api.myRepos().then(setMine).catch(() => {});
  }, []);

  async function start(e: FormEvent) {
    e.preventDefault();
    if (!url.trim()) return;
    setBusy(true);
    setError("");
    try {
      const { repo_id } = await api.createRepo(url.trim());
      navigate(`/r/${repo_id}`);
    } catch (err) {
      setError((err as Error).message);
      setBusy(false);
    }
  }

  async function startSample(s: Sample) {
    if (!s.repo_id) return;
    try {
      const st = await api.createSession(s.repo_id);
      navigate(`/s/${st.session_id}`);
    } catch (err) {
      setError((err as Error).message);
    }
  }

  return (
    <Layout wide>
      <section className="mx-auto max-w-2xl pb-12 pt-10 text-center">
        <h1 className="text-4xl font-semibold tracking-tight sm:text-5xl">
          The interviewer who has <span className="text-accent">read your code</span>.
        </h1>
        <p className="mt-5 text-lg leading-relaxed text-zinc-400">
          Paste a public GitHub repo. Viva asks what a sharp senior engineer would ask, you answer out loud, and every verdict
          comes with receipts: the exact lines of your code.
        </p>
        <form onSubmit={start} className="mt-8 flex flex-col gap-3 sm:flex-row">
          <input
            autoFocus
            value={url}
            onChange={(e) => setUrl(e.target.value)}
            placeholder="https://github.com/you/your-project"
            className="flex-1 rounded-xl border border-zinc-700 bg-zinc-900 px-4 py-3.5 font-mono text-sm text-zinc-100 placeholder:text-zinc-500 focus:border-accent focus:outline-none focus:ring-2 focus:ring-accent/30"
          />
          <Button type="submit" disabled={busy || !url.trim() || budgetOut} className="px-6 py-3.5 text-base">
            {busy && <Spinner className="border-zinc-500 border-t-zinc-950" />} Interview me
          </Button>
        </form>
        <p className="mt-3 text-xs text-zinc-500">Branch and folder links work too. Public repos only. No sign-up.</p>
        {budgetOut && <div className="mt-4"><ErrorNote message="Viva has used up today's demo budget. Please come back tomorrow!" /></div>}
        {error && <div className="mt-4"><ErrorNote message={error} /></div>}
      </section>

      {samples.length > 0 && (
        <section className="mb-14">
          <h2 className="mb-4 text-sm font-medium uppercase tracking-wider text-zinc-500">Or start instantly on a sample</h2>
          <div className="grid gap-4 sm:grid-cols-2">
            {samples.map((s) => (
              <button
                key={s.url}
                onClick={() => startSample(s)}
                disabled={!s.ready}
                className="group rounded-2xl border border-zinc-800 bg-zinc-900/50 p-5 text-left transition hover:border-accent/60 disabled:opacity-50"
              >
                <div className="font-mono text-sm text-zinc-400">{s.owner}/</div>
                <div className="text-lg font-semibold">{s.name}</div>
                <p className="mt-2 line-clamp-2 text-sm text-zinc-400">{s.summary || "Pre-ingested and ready."}</p>
                <span className="mt-4 inline-block text-sm font-medium text-accent group-hover:underline">
                  {s.ready ? "Start interview →" : "Getting ready…"}
                </span>
              </button>
            ))}
          </div>
        </section>
      )}

      {mine.length > 0 && (
        <section className="mb-14">
          <h2 className="mb-4 text-sm font-medium uppercase tracking-wider text-zinc-500">Your repos</h2>
          <div className="flex flex-wrap gap-3">
            {mine.map((r) => (
              <Link key={r.id} to={`/progress/${r.id}`} className="rounded-lg border border-zinc-800 px-3 py-2 text-sm hover:border-zinc-600">
                <span className="font-mono">{r.owner}/{r.name}</span>
                <span className="ml-2 text-zinc-500">{r.sessions} session{r.sessions === 1 ? "" : "s"}</span>
              </Link>
            ))}
          </div>
        </section>
      )}

      <section className="grid gap-6 border-t border-zinc-900 pt-10 sm:grid-cols-3">
        {STEPS.map(([title, body], i) => (
          <div key={title}>
            <div className="mb-2 font-mono text-sm text-accent">0{i + 1}</div>
            <h3 className="font-semibold">{title}</h3>
            <p className="mt-1.5 text-sm leading-relaxed text-zinc-400">{body}</p>
          </div>
        ))}
      </section>
    </Layout>
  );
}
