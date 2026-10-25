"""Structured LLM calls: every response is JSON constrained to, and validated by, a Pydantic model.

Talks to any OpenAI-compatible API (Azure OpenAI's v1 endpoint by default; Gemini, Groq or a local
Ollama work by changing LLM_BASE_URL). Reasoning models (gpt-5 family) take a reasoning effort and
no temperature; for other models leave the effort empty and we send temperature 0.
"""

from typing import TypeVar

import openai
import pydantic

from viva import config, usage

T = TypeVar("T", bound=pydantic.BaseModel)
MAX_TOKENS_CAP = 32_000


class LLMError(RuntimeError):
    pass


class BudgetExceeded(LLMError):
    pass


_client: openai.OpenAI | None = None


def client() -> openai.OpenAI:
    """Shared by embeddings.py, which uses the same endpoint."""
    global _client
    if _client is None:
        if not config.LLM_API_KEY:
            raise LLMError("LLM_API_KEY is not set.")
        _client = openai.OpenAI(base_url=config.LLM_BASE_URL, api_key=config.LLM_API_KEY, max_retries=6, timeout=300)
    return _client


def structured(
    schema: type[T],
    *,
    system: str,
    prompt: str,
    tier: str = "strong",
    effort: str | None = None,
    purpose: str,
    repo_id: int | None = None,
    max_tokens: int = 16_000,
) -> T:
    """One structured call, retried once if the output is cut off or fails validation."""
    if usage.spent_today() >= config.DAILY_BUDGET_USD:
        raise BudgetExceeded("Viva has used up today's demo budget. Please come back tomorrow!")
    model = config.LLM_MODEL_STRONG if tier == "strong" else config.LLM_MODEL_CHEAP
    if effort is not None:  # per-call override, e.g. grading runs at its own effort for latency
        effort = effort or None
    else:
        effort = config.LLM_EFFORT_STRONG if tier == "strong" else config.LLM_EFFORT_CHEAP
    sampling = {"reasoning_effort": effort} if effort else {"temperature": 0}
    messages = [{"role": "system", "content": system}, {"role": "user", "content": prompt}]
    error = "no attempt made"

    for _ in range(2):
        try:
            resp = client().chat.completions.parse(
                model=model, messages=messages, response_format=schema, max_completion_tokens=max_tokens, **sampling
            )
        except openai.LengthFinishReasonError as e:
            _record(model, purpose, e.completion.usage, repo_id)
            error = "output was cut off"
            max_tokens = min(max_tokens * 2, MAX_TOKENS_CAP)
            continue
        except pydantic.ValidationError as e:
            error = str(e)[:1000]
            continue
        except openai.ContentFilterFinishReasonError as e:
            raise LLMError("The provider's content filter blocked this response.") from e
        except openai.BadRequestError as e:
            if "content_filter" in str(e) or "content management policy" in str(e):
                raise LLMError("The provider's content filter blocked this request.") from e
            raise LLMError(f"LLM request rejected: {e.message}") from e
        except openai.APIStatusError as e:
            raise LLMError(f"LLM request failed ({e.status_code}): {e.message}") from e
        except openai.APIConnectionError as e:
            raise LLMError("Couldn't reach the LLM provider.") from e

        _record(model, purpose, resp.usage, repo_id)
        message = resp.choices[0].message
        if message.refusal:
            raise LLMError(f"The model declined: {message.refusal[:200]}")
        if message.parsed is not None:
            return message.parsed
        error = "empty response"
    raise LLMError(f"LLM output unusable after retry: {error}")


def _record(model: str, purpose: str, u, repo_id: int | None) -> None:
    if u is not None:
        usage.record(model, purpose, u.prompt_tokens, u.completion_tokens, repo_id)
