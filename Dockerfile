FROM ghcr.io/astral-sh/uv:python3.12-bookworm-slim

ENV PYTHONUNBUFFERED=1 \
    PATH="/app/.venv/bin:$PATH" \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy

WORKDIR /app

RUN useradd --create-home --shell /usr/sbin/nologin appuser

COPY pyproject.toml uv.lock README.md ./
COPY alembic.ini ./
COPY bate_ponto_bot ./bate_ponto_bot
COPY migrations ./migrations

RUN uv sync --frozen --no-dev \
    && chown -R appuser:appuser /app

USER appuser

CMD ["bate-ponto-bot"]
