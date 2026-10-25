import hljs from "highlight.js/lib/core";
import bash from "highlight.js/lib/languages/bash";
import c from "highlight.js/lib/languages/c";
import cpp from "highlight.js/lib/languages/cpp";
import csharp from "highlight.js/lib/languages/csharp";
import css from "highlight.js/lib/languages/css";
import dockerfile from "highlight.js/lib/languages/dockerfile";
import go from "highlight.js/lib/languages/go";
import ini from "highlight.js/lib/languages/ini";
import java from "highlight.js/lib/languages/java";
import javascript from "highlight.js/lib/languages/javascript";
import json from "highlight.js/lib/languages/json";
import kotlin from "highlight.js/lib/languages/kotlin";
import makefile from "highlight.js/lib/languages/makefile";
import php from "highlight.js/lib/languages/php";
import python from "highlight.js/lib/languages/python";
import ruby from "highlight.js/lib/languages/ruby";
import rust from "highlight.js/lib/languages/rust";
import scala from "highlight.js/lib/languages/scala";
import sql from "highlight.js/lib/languages/sql";
import swift from "highlight.js/lib/languages/swift";
import typescript from "highlight.js/lib/languages/typescript";
import verilog from "highlight.js/lib/languages/verilog";
import xml from "highlight.js/lib/languages/xml";
import yaml from "highlight.js/lib/languages/yaml";
import type { Evidence } from "../api";

const LANGS = { bash, c, cpp, csharp, css, dockerfile, go, ini, java, javascript, json, kotlin, makefile, php, python, ruby, rust, scala, sql, swift, typescript, verilog, xml, yaml };
for (const [name, lang] of Object.entries(LANGS)) hljs.registerLanguage(name, lang);

const BY_EXT: Record<string, string> = {
  py: "python", js: "javascript", mjs: "javascript", cjs: "javascript", jsx: "javascript", ts: "typescript", tsx: "typescript",
  go: "go", rs: "rust", java: "java", kt: "kotlin", kts: "kotlin", scala: "scala", c: "c", h: "c", cc: "cpp", cpp: "cpp",
  hpp: "cpp", cs: "csharp", rb: "ruby", php: "php", swift: "swift", sh: "bash", bash: "bash", sql: "sql", v: "verilog",
  vh: "verilog", sv: "verilog", svh: "verilog", yml: "yaml", yaml: "yaml", json: "json", toml: "ini", html: "xml", xml: "xml",
  css: "css", scss: "css", mk: "makefile",
};

function languageFor(path: string): string | undefined {
  const name = path.split("/").pop()!.toLowerCase();
  if (name.startsWith("dockerfile")) return "dockerfile";
  if (name === "makefile") return "makefile";
  return BY_EXT[name.split(".").pop() ?? ""];
}

function highlight(line: string, lang?: string): string {
  if (lang) {
    try {
      return hljs.highlight(line, { language: lang, ignoreIllegals: true }).value;
    } catch {
      /* fall through to plain text */
    }
  }
  return line.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
}

const TONE = {
  bad: "border-rose-400 bg-rose-500/15",
  good: "border-emerald-400 bg-emerald-500/10",
  neutral: "border-accent bg-accent-soft",
};

export default function CodeSnippet({ ev, tone = "neutral" }: { ev: Evidence; tone?: keyof typeof TONE }) {
  const lang = languageFor(ev.path);
  const range = ev.start_line === ev.end_line ? `${ev.start_line}` : `${ev.start_line}–${ev.end_line}`;
  return (
    <div className="overflow-hidden rounded-lg border border-zinc-800 bg-[#0c0c0e]">
      <div className="flex items-center justify-between gap-3 border-b border-zinc-800 px-3 py-1.5 font-mono text-xs text-zinc-400">
        <span className="truncate">
          {ev.path}
          <span className="text-zinc-600">:</span>
          {range}
        </span>
        <a href={ev.url} target="_blank" rel="noreferrer" className="shrink-0 text-zinc-400 hover:text-zinc-100">
          View on GitHub ↗
        </a>
      </div>
      <pre className="overflow-x-auto py-2 font-mono text-[12.5px] leading-6">
        {ev.snippet.lines.map((line, i) => {
          const n = ev.snippet.start + i;
          const hit = n >= ev.start_line && n <= ev.end_line;
          return (
            <div key={n} className={`flex border-l-2 ${hit ? TONE[tone] : "border-transparent"}`}>
              <span className="w-12 shrink-0 select-none pr-3 text-right text-zinc-600">{n}</span>
              <code className="whitespace-pre pr-4" dangerouslySetInnerHTML={{ __html: highlight(line, lang) || " " }} />
            </div>
          );
        })}
      </pre>
    </div>
  );
}
