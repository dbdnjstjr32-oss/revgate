# syntax=docker/dockerfile:1.7
# RevGate API (FastAPI + SQLAlchemy 2.0/asyncpg + Alembic + WeasyPrint), Python 3.11 like workers/cad.
#   build context = repo root:  docker build -f apps/api/Dockerfile -t revgate/api:dev .
FROM --platform=linux/amd64 python:3.11-slim-bookworm

# WeasyPrint runtime (Pango/HarfBuzz/fontconfig) + Korean & monospace fonts for the approval PDF.
RUN apt-get update && apt-get install -y --no-install-recommends \
    libpango-1.0-0 libpangoft2-1.0-0 libharfbuzz0b libharfbuzz-subset0 libfontconfig1 \
    fonts-nanum fonts-dejavu-core tzdata \
    && rm -rf /var/lib/apt/lists/*

COPY --from=ghcr.io/astral-sh/uv:0.11.16 /uv /uvx /usr/local/bin/

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never \
    UV_PROJECT_ENVIRONMENT=/opt/venv \
    VIRTUAL_ENV=/opt/venv \
    PATH="/opt/venv/bin:${PATH}"

WORKDIR /app/apps/api

# INSTALL_DEV=1 keeps pytest/httpx/psycopg/pypdf for the e2e runner image (same image, `api-tests` service).
ARG INSTALL_DEV=1
COPY packages/contracts/py /app/packages/contracts/py
COPY apps/api/pyproject.toml apps/api/uv.lock apps/api/README.md /app/apps/api/
RUN --mount=type=cache,target=/root/.cache/uv \
    if [ "$INSTALL_DEV" = "1" ]; then uv sync --frozen; else uv sync --frozen --no-dev; fi

COPY packages/contracts/schemas /app/packages/contracts/schemas
COPY apps/api /app/apps/api

RUN python -c "import fastapi, sqlalchemy, asyncpg, alembic, arq, weasyprint, revgate_contracts; print('api runtime OK, weasyprint', weasyprint.__version__)"

EXPOSE 8000
CMD ["sh", "/app/apps/api/docker-entrypoint.sh"]
