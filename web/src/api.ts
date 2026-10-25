export type Verdict = "strong" | "partial" | "vague" | "wrong" | "contradicts_code" | "dont_know" | "skipped";
export type QuestionType = "how_it_works" | "failure_modes" | "design_decision" | "scaling";

export type Evidence = {
  path: string;
  start_line: number;
  end_line: number;
  quote: string;
  url: string;
  snippet: { start: number; lines: string[] };
};
export type Claim = {
  claim: string;
  words: string;
  status: "supported" | "contradicted" | "unverifiable";
  explanation: string;
  evidence: Evidence[];
};
export type KeyPoint = {
  point: string;
  importance: "must" | "nice";
  status: "hit" | "partial" | "missed" | "wrong";
  note: string;
  evidence: Evidence[];
};
export type Grading = {
  verdict: Verdict;
  score: number;
  claims: Claim[];
  key_points: KeyPoint[];
  contradictions: number;
  feedback: string;
  follow_up: string;
  model_answer: string;
  model_answer_citations: Evidence[];
};
export type Attempt = {
  id: number;
  prompt: string;
  answer: string;
  verdict: Verdict;
  score: number | null;
  seconds: number | null;
  follow_up: boolean;
  grading: Grading | null;
};
export type RepoMeta = { id: number; owner: string; name: string; subpath: string; commit: string; github_url: string };
export type Question = { id: number; text: string; type: QuestionType; difficulty: number; open: boolean; area: string };
export type SummaryQuestion = {
  question_id: number;
  text: string;
  area: string;
  type: QuestionType;
  difficulty: number;
  verdict: Verdict;
  score: number | null;
  follow_ups: number;
  contradictions: number;
  attempts?: Attempt[];
};
export type Summary = {
  session_id: number;
  repo_id: number;
  score: number;
  answered: number;
  skipped: number;
  contradictions: number;
  area_scores: Record<string, number>;
  strongest: string | null;
  weakest: string | null;
  share_token: string | null;
  questions: SummaryQuestion[];
};
export type SessionState = {
  session_id: number;
  size: number;
  repo: RepoMeta;
  mode: "question" | "follow_up" | "reveal" | "done";
  n?: number;
  question?: Question;
  prompt?: string;
  attempts?: Attempt[];
  summary?: Summary;
};
export type RepoFile = { path: string; language: string; lines: number; reasons: string[]; infra: boolean };
export type RepoStatus = RepoMeta & {
  status: "pending" | "ingesting" | "ready" | "failed";
  stage: string | null;
  progress: Record<string, number>;
  error: string | null;
  stats: Record<string, unknown> | null;
  questions_status: "pending" | "generating" | "done" | "failed";
  questions_ready: number;
  first_batch: number;
  files?: RepoFile[];
  overview?: string;
  areas?: { name: string; kind: string; status: string }[];
};
export type Sample = { url: string; owner: string; name: string; repo_id: number | null; ready: boolean; summary: string };
export type Progress = {
  repo: RepoMeta;
  readiness: number | null;
  areas: { name: string; score: number | null; attempted: number; total: number }[];
  history: { session_id: number; at: string; readiness: number; session_score: number }[];
  contradictions: number;
  attempted: number;
  total_questions: number;
  sessions: number;
};

async function call<T>(method: string, path: string, body?: unknown): Promise<T> {
  const res = await fetch(`/api${path}`, {
    method,
    headers: body === undefined ? undefined : { "Content-Type": "application/json" },
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  const data = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(typeof data.detail === "string" ? data.detail : `Something went wrong (${res.status}).`);
  return data as T;
}

export const api = {
  config: () => call<{ samples: Sample[]; budget_exhausted: boolean }>("GET", "/config"),
  myRepos: () => call<{ id: number; owner: string; name: string; sessions: number }[]>("GET", "/me/repos"),
  createRepo: (url: string) => call<{ repo_id: number }>("POST", "/repos", { url }),
  repo: (id: number) => call<RepoStatus>("GET", `/repos/${id}`),
  createSession: (repoId: number, focus?: "weak") => call<SessionState>("POST", "/sessions", { repo_id: repoId, focus }),
  session: (id: number) => call<SessionState>("GET", `/sessions/${id}`),
  answer: (id: number, body: { action: "answer" | "idk" | "skip"; answer?: string; seconds?: number }) =>
    call<{ attempt: Attempt | null; state: SessionState }>("POST", `/sessions/${id}/answer`, body),
  next: (id: number) => call<SessionState>("POST", `/sessions/${id}/next`),
  finish: (id: number) => call<SessionState>("POST", `/sessions/${id}/finish`),
  progress: (repoId: number) => call<Progress>("GET", `/progress/${repoId}`),
  report: (token: string) => call<{ repo: RepoMeta; summary: Summary; finished_at: string }>("GET", `/report/${token}`),
};

export const TYPE_LABEL: Record<QuestionType, string> = {
  how_it_works: "How it works",
  failure_modes: "Failure modes",
  design_decision: "Design decision",
  scaling: "Scaling",
};
