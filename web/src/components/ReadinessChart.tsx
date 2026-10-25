import { useState } from "react";
import type { Progress } from "../api";

const ACCENT = "#9085e9"; // validated against the dark surface (lightness band, chroma, >=3:1)
const W = 640;
const H = 220;
const PAD = { top: 16, right: 20, bottom: 32, left: 36 };

/** Readiness after each session: one series, so no legend; hover shows the exact values. */
export function ReadinessChart({ history }: { history: Progress["history"] }) {
  const [hover, setHover] = useState<number | null>(null);
  if (history.length === 0) return null;
  const n = history.length;
  const x = (i: number) => PAD.left + (n === 1 ? (W - PAD.left - PAD.right) / 2 : (i * (W - PAD.left - PAD.right)) / (n - 1));
  const y = (v: number) => PAD.top + ((100 - v) * (H - PAD.top - PAD.bottom)) / 100;
  const path = history.map((h, i) => `${i ? "L" : "M"}${x(i)},${y(h.readiness)}`).join(" ");
  const area = `${path} L${x(n - 1)},${y(0)} L${x(0)},${y(0)} Z`;
  const active = hover ?? n - 1;

  return (
    <div className="relative">
      <svg viewBox={`0 0 ${W} ${H}`} className="w-full" role="img" aria-label="Readiness score after each session">
        {[0, 25, 50, 75, 100].map((v) => (
          <g key={v}>
            <line x1={PAD.left} x2={W - PAD.right} y1={y(v)} y2={y(v)} stroke="#27272a" strokeWidth="1" />
            <text x={PAD.left - 8} y={y(v) + 4} textAnchor="end" className="fill-zinc-500 text-[11px]">
              {v}
            </text>
          </g>
        ))}
        {history.map((_, i) => (
          <text key={i} x={x(i)} y={H - 10} textAnchor="middle" className="fill-zinc-500 text-[11px]">
            {i + 1}
          </text>
        ))}
        <path d={area} fill={ACCENT} opacity="0.1" />
        <path d={path} fill="none" stroke={ACCENT} strokeWidth="2" strokeLinejoin="round" strokeLinecap="round" />
        {hover !== null && <line x1={x(hover)} x2={x(hover)} y1={PAD.top} y2={y(0)} stroke="#52525b" strokeWidth="1" />}
        {history.map((h, i) => (
          <circle key={i} cx={x(i)} cy={y(h.readiness)} r={i === active ? 5 : 4} fill={ACCENT} stroke="#18181b" strokeWidth="2" />
        ))}
        {history.map((_, i) => (
          <rect
            key={i}
            x={x(i) - (W - PAD.left - PAD.right) / Math.max(n - 1, 1) / 2}
            y={0}
            width={(W - PAD.left - PAD.right) / Math.max(n - 1, 1)}
            height={H}
            fill="transparent"
            onMouseEnter={() => setHover(i)}
            onMouseLeave={() => setHover(null)}
          />
        ))}
      </svg>
      {hover !== null && (
        <div
          className="pointer-events-none absolute -translate-x-1/2 rounded-lg border border-zinc-700 bg-zinc-900 px-3 py-2 text-xs shadow-lg"
          style={{ left: `${(x(hover) / W) * 100}%`, top: 0 }}
        >
          <div className="font-medium text-zinc-100">Session {hover + 1}</div>
          <div className="text-zinc-400">Readiness {history[hover].readiness}</div>
          <div className="text-zinc-400">Session score {history[hover].session_score}</div>
        </div>
      )}
      <p className="mt-1 text-center text-xs text-zinc-500">Session</p>
    </div>
  );
}

/** Per-area readiness as horizontal bars (one hue: these are magnitudes, not categories). */
export function AreaBars({ areas }: { areas: Progress["areas"] }) {
  return (
    <ul className="space-y-3">
      {areas.map((a) => (
        <li key={a.name} className="grid grid-cols-[minmax(0,1fr)_auto] items-center gap-x-4 gap-y-1.5">
          <span className="truncate text-sm text-zinc-300" title={a.name}>
            {a.name}
          </span>
          <span className="text-right text-sm tabular-nums text-zinc-400">
            {a.score ?? "–"} <span className="text-xs text-zinc-600">({a.attempted}/{a.total})</span>
          </span>
          <div className="col-span-2 h-2.5 rounded-full bg-zinc-800" title={`${a.name}: ${a.score ?? "not attempted"}`}>
            {a.score !== null && <div className="h-2.5 rounded-full" style={{ width: `${Math.max(a.score, 2)}%`, background: ACCENT }} />}
          </div>
        </li>
      ))}
    </ul>
  );
}
