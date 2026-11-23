# syntax=docker/dockerfile:1.7
# RevGate CAD worker (IMPLEMENTATION_BLUEPRINT §1.2)
#   build context = repo root:  docker build --platform linux/amd64 -f workers/cad/Dockerfile -t revgate/cad-worker:dev .
FROM --platform=linux/amd64 python:3.11-slim-bookworm

# Headless OCCT / VTK link-time libraries (fixed list from the blueprint).
RUN apt-get update && apt-get install -y --no-install-recommends \
    libgl1 libglu1-mesa libxrender1 libxcursor1 libxft2 libxinerama1 \
    libgomp1 libexpat1 libfontconfig1 \
    && rm -rf /var/lib/apt/lists/*

# uv (pinned) — dependency set comes exclusively from workers/cad/uv.lock (--frozen).
COPY --from=ghcr.io/astral-sh/uv:0.11.16 /uv /uvx /usr/local/bin/

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never \
    UV_PROJECT_ENVIRONMENT=/opt/venv \
    VIRTUAL_ENV=/opt/venv \
    PATH="/opt/venv/bin:${PATH}" \
    REVGATE_SCRATCH_DIR=/tmp/revgate_scratch

WORKDIR /app/workers/cad

# 1) Dependency layer: cached until uv.lock / pyproject / contracts change.
#    INSTALL_DEV=0 for a production image (drops pytest/jsonschema).
ARG INSTALL_DEV=1
COPY packages/contracts/py /app/packages/contracts/py
COPY workers/cad/pyproject.toml workers/cad/uv.lock workers/cad/README.md /app/workers/cad/
RUN --mount=type=cache,target=/root/.cache/uv \
    if [ "$INSTALL_DEV" = "1" ]; then uv sync --frozen; else uv sync --frozen --no-dev; fi

# 2) Contracts (JSON Schemas) + engine sources.
COPY packages/contracts/schemas /app/packages/contracts/schemas
COPY workers/cad /app/workers/cad

# Fail the build early if the OCCT runtime cannot be loaded headless.
RUN mkdir -p /tmp/revgate_scratch \
    && python -c "import OCP, numpy, scipy, pygltflib, revgate_contracts; \
from OCP.STEPControl import STEPControl_Reader; from OCP.BRepMesh import BRepMesh_IncrementalMesh; \
print('OCP runtime OK:', OCP.__file__)"

CMD ["python", "-m", "src.cli", "--help"]
