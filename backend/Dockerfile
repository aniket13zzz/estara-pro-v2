FROM python:3.12-slim AS base

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    ESTARA_CONFIG_PATH=/srv/client/client.config.yaml

WORKDIR /srv/backend

COPY pyproject.toml ./
COPY app ./app
RUN pip install --upgrade pip && pip install .

COPY alembic.ini ./
COPY alembic ./alembic

# Dev/check image: adds ruff, mypy, pytest. Used by the `checks` Compose service, which mounts
# ./backend over /srv/backend so the tests and current code are used.
FROM base AS dev
RUN pip install ".[dev]"
CMD ["sh", "-c", "ruff check . && ruff format --check . && mypy && pytest"]

# Runtime image (default target). client/ is mounted read-only at /srv/client by Compose.
FROM base AS runtime
# The uploads volume inherits this ownership on first mount.
RUN useradd --create-home --uid 10001 estara \
    && mkdir -p /srv/data/uploads && chown -R estara /srv/data
USER estara
EXPOSE 8000
CMD ["uvicorn", "app.asgi:app", "--host", "0.0.0.0", "--port", "8000", "--proxy-headers"]
