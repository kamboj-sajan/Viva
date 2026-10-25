CREATE EXTENSION IF NOT EXISTS vector;

-- One row per (repo, commit, subfolder). Repeat runs on the same commit reuse it.
CREATE TABLE IF NOT EXISTS repos (
    id BIGSERIAL PRIMARY KEY,
    owner TEXT NOT NULL,
    name TEXT NOT NULL,
    ref TEXT,
    subpath TEXT NOT NULL DEFAULT '',
    commit_sha TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'pending',  -- pending | ingesting | ready | failed
    stage TEXT,
    progress JSONB NOT NULL DEFAULT '{}',
    error TEXT,
    embed_model TEXT,
    repo_map JSONB,
    stats JSONB,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (owner, name, commit_sha, subpath)
);

-- Every file that passed the filters; content is kept only for included files.
CREATE TABLE IF NOT EXISTS files (
    id BIGSERIAL PRIMARY KEY,
    repo_id BIGINT NOT NULL REFERENCES repos(id) ON DELETE CASCADE,
    path TEXT NOT NULL,
    language TEXT,
    size_bytes INT NOT NULL,
    line_count INT NOT NULL,
    score REAL NOT NULL,
    reasons TEXT[] NOT NULL DEFAULT '{}',
    included BOOLEAN NOT NULL,
    infra BOOLEAN NOT NULL DEFAULT false,
    content TEXT,
    summary TEXT,
    symbols TEXT[] NOT NULL DEFAULT '{}',
    UNIQUE (repo_id, path)
);

CREATE TABLE IF NOT EXISTS chunks (
    id BIGSERIAL PRIMARY KEY,
    repo_id BIGINT NOT NULL REFERENCES repos(id) ON DELETE CASCADE,
    file_id BIGINT NOT NULL REFERENCES files(id) ON DELETE CASCADE,
    path TEXT NOT NULL,
    kind TEXT NOT NULL,
    symbol TEXT,
    start_line INT NOT NULL,
    end_line INT NOT NULL,
    content TEXT NOT NULL,
    search_tsv TSVECTOR NOT NULL,
    -- Untyped so the embedding model can change; a few thousand chunks per repo are scanned exactly.
    embedding VECTOR
);
CREATE INDEX IF NOT EXISTS chunks_repo ON chunks (repo_id);
CREATE INDEX IF NOT EXISTS chunks_tsv ON chunks USING GIN (search_tsv);

CREATE TABLE IF NOT EXISTS llm_usage (
    id BIGSERIAL PRIMARY KEY,
    day DATE NOT NULL DEFAULT CURRENT_DATE,
    model TEXT NOT NULL,
    purpose TEXT NOT NULL,
    input_tokens INT NOT NULL,
    output_tokens INT NOT NULL,
    cost_usd NUMERIC(12, 6) NOT NULL,
    repo_id BIGINT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS llm_usage_day ON llm_usage (day);

ALTER TABLE repos ADD COLUMN IF NOT EXISTS questions_status TEXT NOT NULL DEFAULT 'pending';  -- pending | generating | done | failed

-- Interview topics picked from the repo map, most interview-worthy first.
CREATE TABLE IF NOT EXISTS areas (
    id BIGSERIAL PRIMARY KEY,
    repo_id BIGINT NOT NULL REFERENCES repos(id) ON DELETE CASCADE,
    position INT NOT NULL,
    name TEXT NOT NULL,
    kind TEXT NOT NULL,
    description TEXT NOT NULL,
    files TEXT[] NOT NULL,
    planned_types TEXT[] NOT NULL,
    status TEXT NOT NULL DEFAULT 'pending',  -- pending | generating | done | failed
    stats JSONB,
    error TEXT
);
CREATE INDEX IF NOT EXISTS areas_repo ON areas (repo_id);

-- answer_key: [{point, importance: must|nice, evidence: [{path, start_line, end_line, quote}]}]
CREATE TABLE IF NOT EXISTS questions (
    id BIGSERIAL PRIMARY KEY,
    repo_id BIGINT NOT NULL REFERENCES repos(id) ON DELETE CASCADE,
    area_id BIGINT NOT NULL REFERENCES areas(id) ON DELETE CASCADE,
    type TEXT NOT NULL,  -- how_it_works | failure_modes | design_decision | scaling
    difficulty INT NOT NULL,
    open BOOLEAN NOT NULL,
    text TEXT NOT NULL,
    answer_key JSONB NOT NULL,
    misconceptions JSONB NOT NULL,
    follow_ups JSONB NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS questions_repo ON questions (repo_id);
ALTER TABLE questions ADD COLUMN IF NOT EXISTS embedding VECTOR;  -- for spotting near-duplicate questions

-- Anonymous users (a cookie in the browser, a fixed id on the command line).
CREATE TABLE IF NOT EXISTS users (
    id UUID PRIMARY KEY,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS sessions (
    id BIGSERIAL PRIMARY KEY,
    user_id UUID NOT NULL REFERENCES users(id),
    repo_id BIGINT NOT NULL REFERENCES repos(id) ON DELETE CASCADE,
    size INT NOT NULL DEFAULT 5,
    status TEXT NOT NULL DEFAULT 'active',  -- active | finished
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    finished_at TIMESTAMPTZ
);
CREATE INDEX IF NOT EXISTS sessions_user ON sessions (user_id, repo_id);

-- One row per answer. Follow-ups point at the question's first attempt through parent_id and are
-- graded on the whole exchange, so the latest attempt in a chain is the question's score.
CREATE TABLE IF NOT EXISTS attempts (
    id BIGSERIAL PRIMARY KEY,
    session_id BIGINT NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
    question_id BIGINT NOT NULL REFERENCES questions(id) ON DELETE CASCADE,
    parent_id BIGINT REFERENCES attempts(id) ON DELETE CASCADE,
    prompt TEXT NOT NULL,
    answer TEXT NOT NULL,
    verdict TEXT NOT NULL,  -- strong | partial | vague | wrong | contradicts_code | dont_know | skipped
    score INT,  -- null when skipped
    grading JSONB,
    seconds INT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS attempts_session ON attempts (session_id);
CREATE INDEX IF NOT EXISTS attempts_question ON attempts (question_id);
