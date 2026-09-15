FROM python:3.12-slim

COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv

WORKDIR /app
ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1
ENV PATH="/app/.venv/bin:$PATH"

COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project

COPY app ./app
# init_db() runs the migrations at startup, and upgrade_database resolves
# alembic.ini and the revision directory from the backend root - two levels up
# from app/db/migrations.py, which is /app here. Without them the image starts
# only against a database some other process already migrated.
COPY alembic.ini ./alembic.ini
COPY alembic ./alembic
RUN uv sync --frozen --no-dev

EXPOSE 8000
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
