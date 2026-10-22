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
