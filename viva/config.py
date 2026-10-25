"""All configuration comes from environment variables (a local .env is loaded if present)."""

import os

from dotenv import load_dotenv

load_dotenv()


def _int(name: str, default: int) -> int:
    return int(os.environ.get(name, default))


DATABASE_URL = os.environ.get("DATABASE_URL", "postgresql://viva:viva@localhost:5433/viva")
GITHUB_TOKEN = os.environ.get("GITHUB_TOKEN") or None

# Any OpenAI-compatible endpoint. Default setup: Azure OpenAI's v1 API
# (https://<resource>.openai.azure.com/openai/v1/) with deployments named after their models.
LLM_BASE_URL = os.environ.get("LLM_BASE_URL", "")
LLM_API_KEY = os.environ.get("LLM_API_KEY") or None
LLM_MODEL_STRONG = os.environ.get("LLM_MODEL_STRONG", "gpt-5-mini")  # questions, grading, overview
LLM_MODEL_CHEAP = os.environ.get("LLM_MODEL_CHEAP", "gpt-5-mini")  # file summaries
# Reasoning effort for reasoning models (minimal | low | medium | high). Set empty for
# non-reasoning models such as gpt-4o-mini, which then run at temperature 0.
LLM_EFFORT_STRONG = os.environ.get("LLM_EFFORT_STRONG", "medium")
LLM_EFFORT_CHEAP = os.environ.get("LLM_EFFORT_CHEAP", "minimal")
LLM_EFFORT_GRADE = os.environ.get("LLM_EFFORT_GRADE", "low")  # grading is interactive: keep it fast

EMBED_MODEL = os.environ.get("EMBED_MODEL", "text-embedding-3-small")
EMBED_DIM = _int("EMBED_DIM", 1024)

MAX_FILES = _int("MAX_FILES", 150)
MAX_INFRA_FILES = _int("MAX_INFRA_FILES", 15)
MAX_FILE_BYTES = _int("MAX_FILE_BYTES", 200_000)
MAX_TARBALL_BYTES = _int("MAX_TARBALL_BYTES", 150_000_000)

# Public demo safety
DAILY_BUDGET_USD = float(os.environ.get("DAILY_BUDGET_USD", "5"))
RATE_REPOS_PER_HOUR = _int("RATE_REPOS_PER_HOUR", 10)  # repo submissions per IP
RATE_GRADES_PER_HOUR = _int("RATE_GRADES_PER_HOUR", 80)  # graded answers per IP
# Pre-ingested repos offered on the home page; pin them to a commit so they're always instant.
SAMPLE_REPOS = [u.strip() for u in os.environ.get(
    "SAMPLE_REPOS",
    "https://github.com/pallets/click/tree/2247b35ea1c47c727d7a06e51fa280e12a863ff6,"
    "https://github.com/alexforencich/verilog-uart/tree/1b867e53af738e4a8bc7c839ca2f1c07f40382dc",
).split(",") if u.strip()]

# USD per million tokens (input, output), list prices. Used for spend tracking and the daily budget.
PRICES = {
    "gpt-5-mini": (0.25, 2.00),
    "gpt-4o-mini": (0.15, 0.60),
    "text-embedding-3-small": (0.02, 0.0),
}
