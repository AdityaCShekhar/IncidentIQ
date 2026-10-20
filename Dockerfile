FROM ghcr.io/astral-sh/uv:0.12.19 AS uv-bin

FROM python:3.12-slim-bookworm

COPY --from=uv-bin /uv /uvx /bin/

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PATH="/app/.venv/bin:$PATH"

WORKDIR /app
RUN useradd --create-home --uid 10001 incidentiq

COPY pyproject.toml uv.lock README.md ./
RUN uv sync --frozen --no-dev --no-install-project

COPY app ./app
RUN uv sync --frozen --no-dev --no-editable

COPY alembic.ini ./
COPY migrations ./migrations
USER incidentiq
EXPOSE 8000
CMD ["uvicorn", "incidentiq.main:app", "--host", "0.0.0.0", "--port", "8000", "--no-access-log"]
