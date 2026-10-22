"""Spend tracking for LLM and embedding calls (feeds the daily budget)."""

from viva import config, db


def cost(model: str, input_tokens: int, output_tokens: int) -> float:
    price_in, price_out = config.PRICES.get(model, (0.0, 0.0))
    return (input_tokens * price_in + output_tokens * price_out) / 1_000_000


def record(model: str, purpose: str, input_tokens: int, output_tokens: int, repo_id: int | None = None) -> None:
    db.execute(
        """INSERT INTO llm_usage (model, purpose, input_tokens, output_tokens, cost_usd, repo_id)
           VALUES (%s, %s, %s, %s, %s, %s)""",
        (model, purpose, input_tokens, output_tokens, cost(model, input_tokens, output_tokens), repo_id),
    )


def spent_today() -> float:
    row = db.one("SELECT COALESCE(SUM(cost_usd), 0) AS s FROM llm_usage WHERE day = CURRENT_DATE")
    return float(row["s"])


def spent_on_repo(repo_id: int) -> float:
    row = db.one("SELECT COALESCE(SUM(cost_usd), 0) AS s FROM llm_usage WHERE repo_id = %s", (repo_id,))
    return float(row["s"])
