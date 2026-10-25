import { useCallback, useEffect, useRef, useState } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";
import { api, TYPE_LABEL, type Attempt, type SessionState, type Summary } from "../api";
import AnswerBox, { Grading } from "../components/AnswerBox";
import VerdictPanel from "../components/VerdictPanel";
import { Button, Difficulty, ErrorNote, Kbd, Layout, ScoreRing, Spinner, TypeBadge, VerdictBadge } from "../components/ui";

export default function Interview() {
  const sessionId = Number(useParams().sessionId);
  const [state, setState] = useState<SessionState | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [fresh, setFresh] = useState<number | null>(null); // attempt id to animate
  const verdictRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    api.session(sessionId).then(setState).catch((e) => setError(e.message));
  }, [sessionId]);

  const send = useCallback(
    async (action: "answer" | "idk" | "skip", answer: string | undefined, seconds: number) => {
      setBusy(true);
      setError("");
      try {
        const res = await api.answer(sessionId, { action, answer, seconds });
        setFresh(res.attempt?.id ?? null);
        setState(res.state);
        setTimeout(() => verdictRef.current?.scrollIntoView({ behavior: "smooth", block: "start" }), 50);
      } catch (e) {
        setError((e as Error).message);
      } finally {
        setBusy(false);
      }
    },
    [sessionId],
  );

  const next = useCallback(async () => {
    setBusy(true);
    try {
      setState(await api.next(sessionId));
      setFresh(null);
      window.scrollTo({ top: 0, behavior: "smooth" });
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  }, [sessionId]);

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      const typing = document.activeElement instanceof HTMLTextAreaElement || document.activeElement instanceof HTMLInputElement;
      if (!typing && !busy && state?.mode === "reveal" && (e.key === "n" || e.key === "N") && !e.metaKey && !e.ctrlKey) {
        e.preventDefault();
        next();
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [state?.mode, busy, next]);

  if (!state) return <Layout>{error ? <ErrorNote message={error} /> : <Spinner />}</Layout>;
  if (state.mode === "done" && state.summary) return <SummaryView summary={state.summary} repoName={state.repo.name} />;

  const q = state.question!;
  const attempts = state.attempts ?? [];
  const lastGraded = [...attempts].reverse().find((a) => a.grading);
  const answering = state.mode === "question" || state.mode === "follow_up";

  return (
    <Layout>
      <div className="mb-6 flex items-center justify-between text-sm text-zinc-500">
        <span className="font-mono">{state.repo.owner}/{state.repo.name}</span>
        <span className="flex items-center gap-3">
          Question {state.n} of {state.size}
          <span className="flex gap-1">
            {Array.from({ length: state.size }, (_, i) => (
              <span key={i} className={`h-1.5 w-5 rounded-full ${i < (state.n ?? 0) ? "bg-accent" : "bg-zinc-800"}`} />
            ))}
          </span>
        </span>
      </div>

      <section className="mb-6 rounded-2xl border border-zinc-800 bg-zinc-900/40 p-6">
        <div className="mb-4 flex flex-wrap items-center gap-3">
          <TypeBadge type={q.type} open={q.open} />
          <Difficulty level={q.difficulty} />
          <span className="text-xs text-zinc-500">{q.area}</span>
        </div>
        <p className="text-xl leading-relaxed text-zinc-50">{q.text}</p>
      </section>

      <div className="space-y-8">
        {attempts.map((a, i) => (
          <AttemptBlock
            key={a.id}
            attempt={a}
            index={i}
            reveal={state.mode === "reveal" && a.id === lastGraded?.id}
            animate={a.id === fresh}
            innerRef={i === attempts.length - 1 ? verdictRef : undefined}
          />
        ))}
      </div>

      {error && <div className="mt-6"><ErrorNote message={error} /></div>}

      {answering && (
        <section className="mt-8 space-y-4">
          {state.mode === "follow_up" && (
            <div className="rounded-2xl border border-accent/40 bg-accent-soft p-5">
              <div className="mb-1.5 text-xs font-medium uppercase tracking-wider text-[#c9c3f6]">Follow-up</div>
              <p className="text-lg leading-relaxed text-zinc-50">{state.prompt}</p>
            </div>
          )}
          {busy ? (
            <Grading />
          ) : (
            <AnswerBox
              promptKey={`${q.id}-${attempts.length}`}
              busy={busy}
              onSubmit={(text, s) => send("answer", text, s)}
              onIdk={(s) => send("idk", undefined, s)}
              onSkip={(s) => send("skip", undefined, s)}
            />
          )}
        </section>
      )}

      {state.mode === "reveal" && (
        <div className="mt-10 flex items-center gap-4">
          <Button onClick={next} disabled={busy} className="px-6 py-3 text-base">
            {(state.n ?? 0) >= state.size ? "See your results" : "Next question"} <Kbd>N</Kbd>
          </Button>
        </div>
      )}
    </Layout>
  );
}

function AttemptBlock({
  attempt,
  index,
  reveal,
  animate,
  innerRef,
}: {
  attempt: Attempt;
  index: number;
  reveal: boolean;
  animate: boolean;
  innerRef?: React.Ref<HTMLDivElement>;
}) {
  if (attempt.verdict === "skipped" && !attempt.follow_up) return null;
  return (
    <div ref={innerRef} className="scroll-mt-6 space-y-4">
      {attempt.follow_up && (
        <p className="text-sm text-zinc-500">
          <span className="font-medium text-zinc-400">Follow-up {index}:</span> {attempt.prompt}
        </p>
      )}
      {attempt.answer && (
        <blockquote className="border-l-2 border-zinc-700 pl-4 text-zinc-400">
          <span className="mb-1 block text-xs uppercase tracking-wider text-zinc-600">You said</span>
          {attempt.answer}
        </blockquote>
      )}
      <VerdictPanel attempt={attempt} reveal={reveal} animate={animate} />
    </div>
  );
}

export function SummaryView({ summary, repoName, shared = false }: { summary: Summary; repoName: string; shared?: boolean }) {
  const navigate = useNavigate();
  const [copied, setCopied] = useState(false);
  const [busy, setBusy] = useState(false);
  const shareUrl = summary.share_token ? `${location.origin}/report/${summary.share_token}` : "";

  async function practice(focus?: "weak") {
    setBusy(true);
    const st = await api.createSession(summary.repo_id, focus);
    navigate(`/s/${st.session_id}`);
  }

  return (
    <Layout>
      <div className="mb-8 flex flex-col items-center gap-6 text-center sm:flex-row sm:text-left">
        <ScoreRing score={summary.score} size={136} />
        <div>
          <div className="text-sm text-zinc-500">{shared ? "Interview report" : "Session complete"}</div>
          <h1 className="text-3xl font-semibold tracking-tight">{repoName}</h1>
          <div className="mt-3 flex flex-wrap justify-center gap-x-6 gap-y-2 text-sm sm:justify-start">
            <span>
              <span className="text-2xl font-semibold text-rose-300">{summary.contradictions}</span>{" "}
              <span className="text-zinc-400">contradiction{summary.contradictions === 1 ? "" : "s"} caught</span>
            </span>
            <span>
              <span className="text-2xl font-semibold">{summary.answered}</span> <span className="text-zinc-400">answered</span>
            </span>
            {summary.skipped > 0 && (
              <span>
                <span className="text-2xl font-semibold">{summary.skipped}</span> <span className="text-zinc-400">skipped</span>
              </span>
            )}
          </div>
        </div>
      </div>

      {summary.strongest && summary.weakest && (
        <div className="mb-8 grid gap-4 sm:grid-cols-2">
          <div className="rounded-xl border border-emerald-900/60 bg-emerald-950/20 p-4">
            <div className="text-xs uppercase tracking-wider text-emerald-400/80">Strongest area</div>
            <div className="mt-1 font-medium">{summary.strongest}</div>
            <div className="text-sm text-zinc-400">{summary.area_scores[summary.strongest]}/100</div>
          </div>
          <div className="rounded-xl border border-rose-900/60 bg-rose-950/20 p-4">
            <div className="text-xs uppercase tracking-wider text-rose-400/80">Weakest area</div>
            <div className="mt-1 font-medium">{summary.weakest}</div>
            <div className="text-sm text-zinc-400">{summary.area_scores[summary.weakest]}/100</div>
          </div>
        </div>
      )}

      <ul className="mb-8 divide-y divide-zinc-900 rounded-xl border border-zinc-800">
        {summary.questions.map((item, i) => (
          <li key={item.question_id} className="space-y-2 p-4">
            <div className="flex flex-wrap items-center gap-3">
              <span className="text-sm text-zinc-500">{i + 1}.</span>
              <VerdictBadge verdict={item.verdict} />
              <span className="text-sm tabular-nums text-zinc-300">{item.score ?? "–"}</span>
              <span className="text-xs text-zinc-500">{TYPE_LABEL[item.type]} · {item.area}</span>
              {item.follow_ups > 0 && <span className="text-xs text-zinc-500">{item.follow_ups} follow-up{item.follow_ups > 1 ? "s" : ""}</span>}
            </div>
            <p className="text-zinc-200">{item.text}</p>
            {item.attempts && (
              <div className="space-y-6 pt-2">
                {item.attempts.map((a) => (
                  <div key={a.id} className="space-y-3">
                    {a.follow_up && <p className="text-sm text-zinc-500">Follow-up: {a.prompt}</p>}
                    {a.answer && <blockquote className="border-l-2 border-zinc-700 pl-4 text-sm text-zinc-400">{a.answer}</blockquote>}
                    {a.grading && <VerdictPanel attempt={a} reveal={false} animate={false} />}
                  </div>
                ))}
              </div>
            )}
          </li>
        ))}
      </ul>

      {!shared && (
        <div className="flex flex-wrap gap-3">
          <Button onClick={() => practice("weak")} disabled={busy}>Practice weak spots</Button>
          <Button variant="secondary" onClick={() => practice()} disabled={busy}>New session</Button>
          {shareUrl && (
            <Button
              variant="secondary"
              onClick={() => {
                navigator.clipboard.writeText(shareUrl);
                setCopied(true);
              }}
            >
              {copied ? "Link copied" : "Share report"}
            </Button>
          )}
          <Link to={`/progress/${summary.repo_id}`} className="inline-flex items-center px-3 text-sm text-zinc-400 hover:text-zinc-100">
            Progress →
          </Link>
        </div>
      )}
    </Layout>
  );
}
