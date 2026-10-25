import { useEffect, useState } from "react";
import { useNavigate, useParams } from "react-router-dom";
import { api, type Progress } from "../api";
import { AreaBars, ReadinessChart } from "../components/ReadinessChart";
import { Button, ErrorNote, Layout, Spinner } from "../components/ui";

export default function ProgressPage() {
  const repoId = Number(useParams().repoId);
  const navigate = useNavigate();
  const [p, setP] = useState<Progress | null>(null);
  const [error, setError] = useState("");

  useEffect(() => {
    api.progress(repoId).then(setP).catch((e) => setError(e.message));
  }, [repoId]);

  async function start(focus?: "weak") {
    try {
      const st = await api.createSession(repoId, focus);
      navigate(`/s/${st.session_id}`);
    } catch (e) {
      setError((e as Error).message);
    }
  }

  if (error) return <Layout><ErrorNote message={error} /></Layout>;
  if (!p) return <Layout><Spinner /></Layout>;

  return (
    <Layout>
      <div className="mb-8">
        <div className="font-mono text-sm text-zinc-400">{p.repo.owner}/{p.repo.name}</div>
        <h1 className="mt-1 text-3xl font-semibold tracking-tight">Interview readiness</h1>
      </div>

      <div className="mb-10 flex flex-wrap items-end gap-x-10 gap-y-4">
        <div>
          <div className="text-6xl font-semibold">{p.readiness ?? "–"}</div>
          <div className="mt-1 text-sm text-zinc-500">readiness, weighted by difficulty</div>
        </div>
        <div className="text-sm text-zinc-400">
          <div><span className="text-xl font-semibold text-zinc-100">{p.attempted}</span> of {p.total_questions} questions attempted</div>
          <div><span className="text-xl font-semibold text-zinc-100">{p.sessions}</span> session{p.sessions === 1 ? "" : "s"}</div>
          <div><span className="text-xl font-semibold text-rose-300">{p.contradictions}</span> contradictions caught so far</div>
        </div>
      </div>

      {p.history.length > 0 ? (
        <section className="mb-10 rounded-2xl border border-zinc-800 bg-zinc-900/40 p-5">
          <h2 className="mb-3 text-sm font-medium text-zinc-300">Readiness after each session</h2>
          <ReadinessChart history={p.history} />
        </section>
      ) : (
        <p className="mb-10 text-zinc-500">Finish a session to start your chart.</p>
      )}

      <section className="mb-10">
        <h2 className="mb-4 text-sm font-medium text-zinc-300">By area</h2>
        <AreaBars areas={p.areas} />
      </section>

      <div className="flex flex-wrap gap-3">
        <Button onClick={() => start("weak")}>Practice weak spots</Button>
        <Button variant="secondary" onClick={() => start()}>New session</Button>
      </div>
    </Layout>
  );
}
