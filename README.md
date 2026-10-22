# Viva

Viva interviews you about your own GitHub repos the way a senior engineer drills into the
projects on your resume. Paste a public repo link; Viva reads the code, asks the questions a
sharp interviewer would, and grades your spoken answers (via Wispr Flow) against the actual
code — citing the exact file and lines for every verdict.

**Status:** milestone 2 of 6 — ingestion and question generation from the command line.

## Local setup

Prerequisites: Docker, [uv](https://docs.astral.sh/uv/), Python 3.12+, and an OpenAI-compatible
LLM endpoint (we use Azure OpenAI on an Azure for Students subscription; see below).

```bash
cp .env.example .env          # then fill in LLM_BASE_URL and LLM_API_KEY
docker compose up -d db       # Postgres 17 + pgvector on localhost:5433
uv sync                       # install dependencies
uv run pytest                 # unit tests
```

### Azure OpenAI setup (one time)

Azure for Students only allows a few regions (check with
`az policy assignment list --query "[].parameters"`); `koreacentral` has quota for both models.
Global Standard deployments are pay-per-token, so they cost nothing while idle.

```bash
az group create -n rg-viva -l koreacentral
az cognitiveservices account create -g rg-viva -n <name> -l koreacentral --kind OpenAI --sku S0 --custom-domain <name> --yes
az cognitiveservices account deployment create -g rg-viva -n <name> --deployment-name gpt-5-mini \
  --model-name gpt-5-mini --model-version 2025-08-07 --model-format OpenAI --sku-name GlobalStandard --sku-capacity 250
az cognitiveservices account deployment create -g rg-viva -n <name> --deployment-name text-embedding-3-small \
  --model-name text-embedding-3-small --model-version 1 --model-format OpenAI --sku-name GlobalStandard --sku-capacity 500
az cognitiveservices account keys list -g rg-viva -n <name> --query key1 -o tsv   # -> LLM_API_KEY
# LLM_BASE_URL=https://<name>.openai.azure.com/openai/v1/
```

To tear it all down: `az group delete -n rg-viva`.

## Command line

```bash
uv run python -m viva ingest https://github.com/owner/repo          # ingest + generate questions (cached per commit)
uv run python -m viva ingest https://github.com/o/r/tree/main/src   # a branch and subfolder
uv run python -m viva ingest <url> --force                          # re-ingest the same commit
uv run python -m viva ingest <url> --no-questions                   # ingest only
uv run python -m viva questions <id> [--brief] # interview questions with answer keys and citations
uv run python -m viva questions <id> --regenerate   # throw them away and write new ones
uv run python -m viva repos                    # list ingested repos and their status
uv run python -m viva files <id> [--all]       # ranked files and why (--all shows ones cut by the cap)
uv run python -m viva map <id>                 # per-file summaries + architecture overview
uv run python -m viva chunks <id> <path> [--show]   # a file's chunks with GitHub line links
uv run python -m viva search <id> <query…>     # hybrid keyword + vector search
uv run python -m viva verify <id>              # re-download files and check every chunk's line numbers
```

## How ingestion works

1. **Resolve** the link (any form: `owner/repo`, `/tree/<branch>/<folder>`, `/blob/...`, SSH) to a
   commit SHA. Everything is pinned to that commit and cached by (repo, commit, subfolder).
2. **Download** the tarball via the GitHub API. It is read in memory; nothing from the repo is
   extracted to disk or executed.
3. **Filter** out binaries, lockfiles, vendored/build/generated code, files over 200 KB, and
   anything that looks like a secret (`.env*`, keys, credential files, embedded private keys or
   API tokens). Secret files are never stored or shown.
4. **Rank** by interview value: entry points, files many others import (imports are resolved as
   paths for JS/TS and Python, by name elsewhere, by module instantiation for Verilog), README
   mentions, core directories; tests, examples and re-export barrels rank lower. Up to 15
   infra files (Dockerfiles, CI, Kubernetes, Terraform, Makefiles) always get a slot. Cap: 150.
5. **Chunk** by function/class/module with tree-sitter (16 languages) or a keyword-matching
   chunker for Verilog/SystemVerilog, falling back to line windows. Line numbers are exact:
   `verify` checks them against GitHub.
6. **Index**: identifier-aware keywords (`parseHTTPRequest` ≈ `parse_http_request`) in a Postgres
   text index plus text-embedding-3-small vectors in pgvector, merged with reciprocal rank fusion.
7. **Map**: a summary per file (cheap tier, batched), then an architecture overview from the
   summaries, README and last 50 commit messages (strong tier).

Typical cost and time on gpt-5-mini: about $0.04 and 50 s for a 57-file repo, $0.07 and 50 s
for a 107-file repo. A repeat run on the same commit is instant.

## How questions are generated

1. **Areas**: the strong model reads the repo map and picks 6-10 interview areas (core flows, key
   components, failure handling, data model, deployment), each with the 1-6 files that matter.
   If the repo has deploy/CI files and no deployment area was picked, one is added.
2. **Question types** are planned in code so the whole repo follows the mix: about 40% how it
   works, 25% failure modes, 20% design decisions, 15% scaling, matched to each area's kind.
3. **Per area**, the model sees only that area's code with line numbers (small files whole, big
   files as their most relevant chunks, ~12K tokens) and writes 2-3 questions. Each has a type,
   difficulty 1-3, an answer key of 2-5 key points (must-have or nice-to-have, each with a file,
   line range and exact quote), likely misconceptions and two follow-ups. Design and scaling
   questions are marked open: graded on soundness and consistency with the code.
4. **Validation in code** (`viva/citations.py`): every citation must name an ingested file and
   real lines whose text contains the quote after collapsing whitespace. If the quote is real but
   the lines are off, the citation is moved to where the quote actually is, only when it occurs
   exactly once in the file. Failing key points are dropped; a question needs at least two
   verified must-have points (open questions: at least one point). Questions that name a file or
   line number are dropped, and so are near-duplicates of questions already stored (embedding
   similarity ≥ 0.72).
5. Areas run in parallel, most important first, so the first five questions are ready about a
   minute after ingestion while the rest finish in the background. Roughly $0.13 per repo on
   gpt-5-mini.

## Environment variables

| Variable | Default | Notes |
|---|---|---|
| `LLM_BASE_URL` | — | required. Any OpenAI-compatible endpoint; Azure: `https://<name>.openai.azure.com/openai/v1/` |
| `LLM_API_KEY` | — | required |
| `LLM_MODEL_STRONG` / `LLM_MODEL_CHEAP` | `gpt-5-mini` / `gpt-5-mini` | model or Azure deployment names |
| `LLM_EFFORT_STRONG` / `LLM_EFFORT_CHEAP` | `medium` / `minimal` | reasoning effort; empty for non-reasoning models (then temperature 0) |
| `EMBED_MODEL` / `EMBED_DIM` | `text-embedding-3-small` / `1024` | served by the same endpoint |
| `GITHUB_TOKEN` | — | optional locally (60 API calls/hour, ~3 per ingest); needed in production |
| `DATABASE_URL` | `postgresql://viva:viva@localhost:5433/viva` | |
| `MAX_FILES` / `MAX_INFRA_FILES` / `MAX_FILE_BYTES` | `150` / `15` / `200000` | |

## Deploy

Planned for milestone 5: one Docker image (FastAPI serving the built React app), with Neon
Postgres (free tier).

## Notes

- `tree-sitter` is pinned below 0.26: 0.26.0 corrupts parse trees on macOS/Python 3.13
  (segfaults when reading nodes).
