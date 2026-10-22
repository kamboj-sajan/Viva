"""Text embeddings through the same OpenAI-compatible endpoint as the LLM (text-embedding-3-small)."""

from typing import Callable

import openai

from viva import config, llm, usage

MAX_BATCH = 256
MAX_BATCH_CHARS = 300_000
MAX_TEXT_CHARS = 16_000  # stays under the 8191-token input limit even for dense code


class EmbeddingError(RuntimeError):
    pass


def embed(
    texts: list[str],
    input_type: str = "document",  # kept for providers that distinguish queries; OpenAI models don't
    repo_id: int | None = None,
    on_progress: Callable[[int], None] | None = None,
) -> list[list[float]]:
    texts = [t[:MAX_TEXT_CHARS] or " " for t in texts]
    out: list[list[float]] = []
    for batch in _batches(texts):
        try:
            resp = llm.client().embeddings.create(model=config.EMBED_MODEL, input=batch, dimensions=config.EMBED_DIM)
        except (openai.OpenAIError, llm.LLMError) as e:
            raise EmbeddingError(f"Embedding request failed: {e}") from e
        usage.record(config.EMBED_MODEL, "embed", resp.usage.prompt_tokens, 0, repo_id)
        out.extend(d.embedding for d in sorted(resp.data, key=lambda d: d.index))
        if on_progress:
            on_progress(len(out))
    return out


def _batches(texts: list[str]):
    batch, chars = [], 0
    for t in texts:
        if batch and (len(batch) >= MAX_BATCH or chars + len(t) > MAX_BATCH_CHARS):
            yield batch
            batch, chars = [], 0
        batch.append(t)
        chars += len(t)
    if batch:
        yield batch
