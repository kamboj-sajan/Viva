import { useEffect, useRef, useState } from "react";
import { Button, Kbd, Spinner } from "./ui";

const STEPS = ["Pulling out the claims you made", "Searching your code", "Checking every citation", "Writing feedback"];

export function Grading() {
  const [step, setStep] = useState(0);
  useEffect(() => {
    const id = setInterval(() => setStep((s) => Math.min(s + 1, STEPS.length - 1)), 3200);
    return () => clearInterval(id);
  }, []);
  return (
    <div className="flex items-center gap-3 rounded-xl border border-zinc-800 bg-zinc-900/60 px-4 py-4">
      <Spinner />
      <span className="shimmer text-sm font-medium">{STEPS[step]}…</span>
    </div>
  );
}

function clock(seconds: number) {
  return `${Math.floor(seconds / 60)}:${String(seconds % 60).padStart(2, "0")}`;
}

/** The answer box: autofocused so you can hold the Wispr key and talk straight away. */
export default function AnswerBox({
  promptKey,
  busy,
  onSubmit,
  onIdk,
  onSkip,
}: {
  promptKey: string;
  busy: boolean;
  onSubmit: (text: string, seconds: number) => void;
  onIdk: (seconds: number) => void;
  onSkip: (seconds: number) => void;
}) {
  const [text, setText] = useState("");
  const [elapsed, setElapsed] = useState(0);
  const started = useRef(Date.now());
  const box = useRef<HTMLTextAreaElement>(null);

  useEffect(() => {
    setText("");
    started.current = Date.now();
    setElapsed(0);
    box.current?.focus();
    const id = setInterval(() => setElapsed(Math.round((Date.now() - started.current) / 1000)), 1000);
    return () => clearInterval(id);
  }, [promptKey]);

  const seconds = () => Math.round((Date.now() - started.current) / 1000);
  const submit = () => text.trim() && !busy && onSubmit(text.trim(), seconds());

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (busy || e.metaKey || e.ctrlKey || e.altKey) return;
      const typing = document.activeElement === box.current;
      if (e.key === "Escape" && typing) box.current?.blur();
      if (!typing && (e.key === "d" || e.key === "D")) {
        e.preventDefault();
        onIdk(seconds());
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [busy, onIdk]);

  return (
    <div className="space-y-3">
      <div className="relative">
        <textarea
          ref={box}
          value={text}
          onChange={(e) => setText(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === "Enter" && (e.metaKey || e.ctrlKey)) {
              e.preventDefault();
              submit();
            }
          }}
          disabled={busy}
          rows={7}
          placeholder="Hold your Wispr Flow key and answer out loud, like you would in the interview…"
          className="w-full resize-y rounded-xl border border-zinc-700 bg-zinc-900 px-4 py-3.5 text-[15px] leading-relaxed text-zinc-100 placeholder:text-zinc-500 focus:border-accent focus:outline-none focus:ring-2 focus:ring-accent/30 disabled:opacity-60"
        />
        <span className="pointer-events-none absolute right-3 top-3 font-mono text-xs tabular-nums text-zinc-500">{clock(elapsed)}</span>
      </div>
      <div className="flex flex-wrap items-center justify-between gap-3">
        <p className="text-xs text-zinc-500">
          🎙 Hold your Wispr key and just talk; it types into this box. <Kbd>⌘/Ctrl</Kbd> <Kbd>Enter</Kbd> submits · <Kbd>Esc</Kbd> then <Kbd>D</Kbd> for "I don't know"
        </p>
        <div className="flex gap-2">
          <Button variant="ghost" onClick={() => onSkip(seconds())} disabled={busy}>
            Skip
          </Button>
          <Button variant="secondary" onClick={() => onIdk(seconds())} disabled={busy}>
            I don't know
          </Button>
          <Button onClick={submit} disabled={busy || !text.trim()}>
            {busy ? <Spinner className="border-zinc-500 border-t-zinc-950" /> : null} Submit
          </Button>
        </div>
      </div>
    </div>
  );
}
