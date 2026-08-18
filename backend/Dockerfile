FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    UV_LINK_MODE=copy

WORKDIR /app

RUN pip install --no-cache-dir uv

COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project

COPY app ./app
COPY alembic ./alembic
COPY alembic.ini ./alembic.ini
COPY scripts ./scripts

RUN mkdir -p /app/data/uploads /app/data/chroma

EXPOSE 8000

CMD ["sh", "-c", "if [ \"${RUN_MIGRATIONS:-false}\" = \"true\" ]; then .venv/bin/alembic upgrade head; fi; exec .venv/bin/uvicorn app.main:app --host 0.0.0.0 --port 8000 --workers 1"]
