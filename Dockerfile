# One image: the React app is built here and served by FastAPI.
FROM --platform=$BUILDPLATFORM node:22-slim AS web
WORKDIR /web
COPY web/package.json web/package-lock.json ./
RUN npm ci
COPY web/ ./
RUN npm run build

FROM python:3.13-slim
ENV PYTHONUNBUFFERED=1 UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy
COPY --from=ghcr.io/astral-sh/uv:0.12.24 /uv /usr/local/bin/uv
WORKDIR /app
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project
COPY viva/ ./viva/
COPY evals/ ./evals/
COPY --from=web /web/dist ./web/dist
EXPOSE 8000
CMD ["/app/.venv/bin/uvicorn", "viva.app:app", "--host", "0.0.0.0", "--port", "8000"]
