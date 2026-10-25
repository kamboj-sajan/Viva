import { useEffect, useState, type ButtonHTMLAttributes, type ReactNode } from "react";
import { Link } from "react-router-dom";
import { TYPE_LABEL, type QuestionType, type Verdict } from "../api";

export function Layout({ children, wide = false }: { children: ReactNode; wide?: boolean }) {
  return (
    <div className="min-h-screen">
      <header className="border-b border-zinc-900">
        <div className={`mx-auto flex items-center justify-between px-5 py-4 ${wide ? "max-w-5xl" : "max-w-3xl"}`}>
          <Link to="/" className="flex items-center gap-2 text-lg font-semibold tracking-tight">
            <Logo /> Viva
          </Link>
          <span className="hidden text-sm text-zinc-500 sm:block">The interviewer who has read your code</span>
        </div>
      </header>
      <main className={`mx-auto px-5 pb-24 pt-8 ${wide ? "max-w-5xl" : "max-w-3xl"}`}>{children}</main>
    </div>
  );
}

export function Logo({ size = 22 }: { size?: number }) {
  return (
    <svg width={size} height={size} viewBox="0 0 32 32" aria-hidden>
      <rect width="32" height="32" rx="8" fill="#18181b" />
      <path d="M8 9l8 15 8-15" fill="none" stroke="#9085e9" strokeWidth="3.2" strokeLinecap="round" strokeLinejoin="round" />
    </svg>
  );
}

export function Button({
  variant = "primary",
  className = "",
  ...props
}: ButtonHTMLAttributes<HTMLButtonElement> & { variant?: "primary" | "secondary" | "ghost" }) {
  const styles = {
    primary: "bg-accent text-zinc-950 hover:bg-[#a59cf0] disabled:bg-zinc-700 disabled:text-zinc-400",
    secondary: "border border-zinc-700 bg-zinc-900 text-zinc-100 hover:border-zinc-500 disabled:text-zinc-500",
    ghost: "text-zinc-400 hover:text-zinc-100 disabled:text-zinc-600",
  }[variant];
  return (
    <button
      className={`inline-flex items-center justify-center gap-2 rounded-lg px-4 py-2.5 text-sm font-medium transition-colors disabled:cursor-not-allowed ${styles} ${className}`}
      {...props}
    />
  );
}

export function Kbd({ children }: { children: ReactNode }) {
  return (
    <kbd className="rounded border border-zinc-700 bg-zinc-800/80 px-1.5 py-0.5 font-mono text-[11px] text-zinc-300">
      {children}
    </kbd>
  );
}

export function Spinner({ className = "" }: { className?: string }) {
  return (
    <span
      className={`inline-block h-4 w-4 animate-spin rounded-full border-2 border-zinc-600 border-t-accent ${className}`}
      aria-hidden
    />
  );
}

export const VERDICT: Record<Verdict, { label: string; className: string }> = {
  strong: { label: "Strong", className: "bg-emerald-400 text-emerald-950" },
  partial: { label: "Partial", className: "bg-amber-300 text-amber-950" },
  vague: { label: "Vague", className: "bg-amber-200 text-amber-950" },
  wrong: { label: "Wrong", className: "bg-rose-400 text-rose-950" },
  contradicts_code: { label: "Contradicts your code", className: "bg-rose-600 text-white" },
  dont_know: { label: "I don't know", className: "bg-sky-300 text-sky-950" },
  skipped: { label: "Skipped", className: "bg-zinc-700 text-zinc-200" },
};

export function VerdictBadge({ verdict, big = false, animate = false }: { verdict: Verdict; big?: boolean; animate?: boolean }) {
  const v = VERDICT[verdict];
  return (
    <span
      className={`inline-flex items-center rounded-md font-semibold uppercase tracking-wide ${v.className} ${
        big ? "px-3 py-1.5 text-sm" : "px-2 py-0.5 text-[11px]"
      } ${animate ? "animate-pop" : ""} ${animate && verdict === "contradicts_code" ? "animate-alarm" : ""}`}
    >
      {v.label}
    </span>
  );
}

export function TypeBadge({ type, open }: { type: QuestionType; open?: boolean }) {
  return (
    <span className="inline-flex items-center gap-1.5 rounded-md border border-accent/40 bg-accent-soft px-2 py-0.5 text-xs font-medium text-[#c9c3f6]">
      {TYPE_LABEL[type]}
      {open && <span className="text-zinc-400">· open-ended</span>}
    </span>
  );
}

export function Difficulty({ level }: { level: number }) {
  return (
    <span className="inline-flex items-center gap-1 text-xs text-zinc-400" title={`Difficulty ${level} of 3`}>
      {[1, 2, 3].map((i) => (
        <span key={i} className={`h-1.5 w-4 rounded-full ${i <= level ? "bg-zinc-300" : "bg-zinc-700"}`} />
      ))}
      <span className="ml-1">{["", "Warm-up", "Solid", "Deep"][level]}</span>
    </span>
  );
}

export function CountUp({ to, duration = 900 }: { to: number; duration?: number }) {
  const [value, setValue] = useState(0);
  useEffect(() => {
    let frame = 0;
    const start = performance.now();
    const tick = (now: number) => {
      const t = Math.min(1, (now - start) / duration);
      setValue(Math.round(to * (1 - Math.pow(1 - t, 3))));
      if (t < 1) frame = requestAnimationFrame(tick);
    };
    frame = requestAnimationFrame(tick);
    return () => cancelAnimationFrame(frame);
  }, [to, duration]);
  return <>{value}</>;
}

export function ScoreRing({ score, size = 120 }: { score: number; size?: number }) {
  const r = (size - 12) / 2;
  const c = 2 * Math.PI * r;
  return (
    <div className="relative inline-flex items-center justify-center" style={{ width: size, height: size }}>
      <svg width={size} height={size} className="-rotate-90">
        <circle cx={size / 2} cy={size / 2} r={r} fill="none" stroke="#27272a" strokeWidth="8" />
        <circle
          cx={size / 2}
          cy={size / 2}
          r={r}
          fill="none"
          stroke="#9085e9"
          strokeWidth="8"
          strokeLinecap="round"
          strokeDasharray={c}
          strokeDashoffset={c * (1 - score / 100)}
          style={{ transition: "stroke-dashoffset 1s ease-out" }}
        />
      </svg>
      <div className="absolute text-center">
        <div className="text-3xl font-semibold">
          <CountUp to={score} />
        </div>
        <div className="text-xs text-zinc-500">out of 100</div>
      </div>
    </div>
  );
}

export function ErrorNote({ message }: { message: string }) {
  return <div className="rounded-lg border border-rose-900 bg-rose-950/40 px-4 py-3 text-sm text-rose-200">{message}</div>;
}
