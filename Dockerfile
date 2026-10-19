FROM python:3.12-slim-bookworm

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app
RUN useradd --create-home --uid 10001 incidentiq

COPY pyproject.toml README.md ./
COPY app ./app
RUN pip install --no-cache-dir .

COPY alembic.ini ./
COPY migrations ./migrations
USER incidentiq
EXPOSE 8000
CMD ["uvicorn", "incidentiq.main:app", "--host", "0.0.0.0", "--port", "8000", "--no-access-log"]
