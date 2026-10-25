import { useState } from "react";
import type { Attempt, Claim, Grading, KeyPoint } from "../api";
import CodeSnippet from "./CodeSnippet";
import { CountUp, VerdictBadge } from "./ui";

const CLAIM_STYLE: Record<Claim["status"], { icon: string; label: string; className: string }> = {
  contradicted: { icon: "✗", label: "Contradicted by your code", className: "border-rose-500/60 bg-rose-950/30" },
  supported: { icon: "✓", label: "Backed by your code", className: "border-emerald-700/50 bg-emerald-950/20" },
  unverifiable: { icon: "?", label: "Can't verify from the code", className: "border-zinc-800 bg-zinc-900/40" },
};
const POINT_ICON: Record<KeyPoint["status"], string> = { hit: "✓", partial: "◐", missed: "○", wrong: "✗" };
const POINT_COLOR: Record<KeyPoint["status"], string> = {
  hit: "text-emerald-400",
  partial: "text-amber-300",
  missed: "text-zinc-500",
  wrong: "text-rose-400",
};
const ORDER: Claim["status"][] = ["contradicted", "supported", "unverifiable"];

/** The verdict for one answer. The answer key and model answer only appear once `reveal` is set,
 * so they don't give away a follow-up that's still coming. */
export default function VerdictPanel({ attempt, reveal, animate }: { attempt: Attempt; reveal: boolean; animate: boolean }) {
  const g = attempt.grading;
  if (!g) {
    return (
      <p className="text-sm text-zinc-500">
        {attempt.verdict === "skipped" ? "You skipped this follow-up." : "You didn't know this follow-up, so your earlier score stands."}
      </p>
    );
  }
  const claims = [...g.claims].sort((a, b) => ORDER.indexOf(a.status) - ORDER.indexOf(b.status));
  const shown = new Set<string>(); // the same lines cited twice are shown once
  const stagger = (i: number) => (animate ? { animationDelay: `${0.35 + i * 0.12}s` } : undefined);
  return (
    <div className="space-y-5">
      <div className={`flex flex-wrap items-center gap-4 ${animate && g.verdict === "contradicts_code" ? "animate-shake" : ""}`}>
        <VerdictBadge verdict={g.verdict} big animate={animate} />
        <span className="text-2xl font-semibold tabular-nums">
          {animate ? <CountUp to={g.score} /> : g.score}
          <span className="text-base font-normal text-zinc-500">/100</span>
        </span>
        {g.contradictions > 0 && (
          <span className="text-sm text-rose-300">
            {g.contradictions} contradiction{g.contradictions > 1 ? "s" : ""} caught
          </span>
        )}
        {attempt.seconds != null && <span className="text-sm text-zinc-500">answered in {attempt.seconds}s</span>}
      </div>

      {claims.length > 0 && (
        <div className="space-y-3">
          <h4 className="text-xs font-medium uppercase tracking-wider text-zinc-500">What you said, checked against the code</h4>
          {claims.map((c, i) => (
            <div key={i} className={`space-y-3 rounded-xl border p-4 ${CLAIM_STYLE[c.status].className} ${animate ? "animate-rise" : ""}`} style={stagger(i)}>
              <div className="flex items-start gap-3">
                <span
                  className={`mt-0.5 font-mono text-sm ${c.status === "contradicted" ? "text-rose-400" : c.status === "supported" ? "text-emerald-400" : "text-zinc-500"}`}
                >
                  {CLAIM_STYLE[c.status].icon}
                </span>
                <div className="space-y-1">
                  <p className="text-[15px] text-zinc-100">“{c.words || c.claim}”</p>
                  <p className="text-sm text-zinc-400">
                    <span className="font-medium text-zinc-300">{CLAIM_STYLE[c.status].label}.</span> {c.explanation}
                  </p>
                </div>
              </div>
              {c.evidence.slice(0, 1).map((ev, j) => {
                const key = `${ev.path}:${ev.start_line}-${ev.end_line}`;
                if (shown.has(key)) {
                  return (
                    <a key={j} href={ev.url} target="_blank" rel="noreferrer" className="ml-7 inline-block font-mono text-xs text-zinc-400 hover:text-zinc-100">
                      Same lines as above: {ev.path}:{ev.start_line}–{ev.end_line} ↗
                    </a>
                  );
                }
                shown.add(key);
                return <CodeSnippet key={j} ev={ev} tone={c.status === "contradicted" ? "bad" : "good"} />;
              })}
            </div>
          ))}
        </div>
      )}

      <div className={`rounded-xl border border-zinc-800 bg-zinc-900/60 p-4 ${animate ? "animate-rise" : ""}`} style={stagger(claims.length)}>
        <h4 className="mb-1.5 text-xs font-medium uppercase tracking-wider text-zinc-500">Interviewer</h4>
        <p className="leading-relaxed text-zinc-200">{g.feedback}</p>
      </div>

      {reveal ? (
        <Reveal g={g} />
      ) : (
        <p className="text-sm text-zinc-500">
          Key points covered so far: {g.key_points.filter((p) => p.status === "hit").length}/{g.key_points.length}. The full answer key appears after the follow-up.
        </p>
      )}
    </div>
  );
}

function Reveal({ g }: { g: Grading }) {
  const [open, setOpen] = useState<number | null>(null);
  return (
    <div className="space-y-5 animate-rise">
      <div>
        <h4 className="mb-2 text-xs font-medium uppercase tracking-wider text-zinc-500">Answer key</h4>
        <ul className="space-y-1.5">
          {g.key_points.map((p, i) => (
            <li key={i}>
              <button className="flex w-full items-start gap-3 rounded-lg px-2 py-1.5 text-left hover:bg-zinc-900" onClick={() => setOpen(open === i ? null : i)}>
                <span className={`mt-0.5 font-mono text-sm ${POINT_COLOR[p.status]}`}>{POINT_ICON[p.status]}</span>
                <span className="flex-1 text-sm text-zinc-300">
                  {p.point}
                  {p.importance === "nice" && <span className="ml-2 text-xs text-zinc-500">bonus</span>}
                </span>
                <span className="text-xs text-zinc-600">{open === i ? "hide code" : "show code"}</span>
              </button>
              {open === i && (
                <div className="mt-2 space-y-2 pl-8">
                  {p.evidence.map((ev, j) => (
                    <CodeSnippet key={j} ev={ev} />
                  ))}
                </div>
              )}
            </li>
          ))}
        </ul>
      </div>
      {g.verdict !== "strong" && g.model_answer && (
        <div className="rounded-xl border border-accent/30 bg-accent-soft p-4">
          <h4 className="mb-1.5 text-xs font-medium uppercase tracking-wider text-[#c9c3f6]">A strong answer</h4>
          <p className="leading-relaxed text-zinc-200">{g.model_answer}</p>
          {g.model_answer_citations.length > 0 && (
            <div className="mt-3 flex flex-wrap gap-2">
              {g.model_answer_citations.map((ev, i) => (
                <a key={i} href={ev.url} target="_blank" rel="noreferrer" className="rounded border border-zinc-700 px-2 py-0.5 font-mono text-xs text-zinc-300 hover:border-zinc-400">
                  [{i + 1}] {ev.path}:{ev.start_line}
                </a>
              ))}
            </div>
          )}
        </div>
      )}
    </div>
  );
}
