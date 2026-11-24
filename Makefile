# RevGate monorepo tasks (POSIX make: Linux / macOS / WSL with Docker integration / Git Bash + make).
#   make dev       Postgres 16 + Redis 7 + MinIO (3 buckets + CORS) up and healthy
#   make stack     + api (FastAPI, migrates on start) + cad-worker (ARQ: 4 queues)
#   make codegen   JSON Schema SSOT -> TypeScript + Pydantic v2 (+ verification)
#   make test-cad  golden STEP suite inside the linux/amd64 CAD worker
#   make test-api  end-to-end suite against the running stack (real Postgres/Redis/MinIO/worker)
#   make test-web  apps/web typecheck + lint + Vitest + next build + Playwright full-stack browser E2E
# Override tools when needed, e.g. from WSL without Docker integration: make stack DOCKER=docker.exe

.DEFAULT_GOAL := help
SHELL := /bin/sh

DOCKER  ?= docker
PNPM    ?= pnpm
COMPOSE ?= $(DOCKER) compose -f infra/docker-compose.yml
CAD     := $(COMPOSE) --profile cad
APP     := $(COMPOSE) --profile app
TEST    := $(COMPOSE) --profile test

.PHONY: help install dev stack down infra-status logs codegen contracts-check cad-build api-build golden migrate \
        test-cad test-api web-check test-web test glb-validate clean

help: ## List targets
	@awk 'BEGIN {FS = ":.*## "} /^[a-zA-Z0-9_-]+:.*## / {printf "  %-16s %s\n", $$1, $$2}' $(MAKEFILE_LIST)

node_modules/.modules.yaml: package.json pnpm-lock.yaml packages/contracts/package.json
	$(PNPM) install --frozen-lockfile

install: node_modules/.modules.yaml ## pnpm workspace install (frozen lockfile)

dev: ## Start local infra and wait until healthy (api/web run on the host)
	$(COMPOSE) up -d --wait postgres redis minio
	$(COMPOSE) run --rm minio-init
	@echo ""
	@echo "  Postgres  postgresql://revgate:revgate@localhost:5432/revgate"
	@echo "  Redis     redis://localhost:6379/0"
	@echo "  MinIO     http://localhost:9000  (console http://localhost:9001, revgate / revgate-dev-secret)"
	@echo "  buckets   revgate-raw-cad  revgate-viewer-derived  revgate-audit-proofs"

stack: dev ## dev + api (http://localhost:8000, alembic upgrade head on start) + cad-worker (ARQ)
	$(APP) up -d --wait api
	$(APP) up -d cad-worker
	@echo "  API       http://localhost:8000/health   docs http://localhost:8000/docs"

down: ## Stop everything (volumes are kept)
	$(TEST) down

infra-status: ## Show container health
	$(TEST) ps -a

logs: ## Follow api + cad-worker logs
	$(APP) logs -f api cad-worker

codegen: install ## Generate TS types + Pydantic models from packages/contracts/schemas and verify them
	$(PNPM) codegen

contracts-check: install ## Fail when generated contracts drift from the schemas (CI)
	$(PNPM) contracts:check

cad-build: ## Build the CAD worker image (linux/amd64, python 3.11, uv, cadquery-ocp, arq)
	$(CAD) build cad-worker

api-build: ## Build the API image (python 3.11, uv, FastAPI, WeasyPrint)
	$(APP) build api

golden: cad-build ## Regenerate the 5 golden STEP pairs (byte-reproducible)
	$(CAD) run --rm --no-deps cad-worker python tests/gen_golden_steps.py

migrate: ## alembic upgrade head (schema owner) — the api container also does this on start
	$(APP) run --rm --no-deps api alembic upgrade head

test-cad: cad-build ## Golden STEP regression + engine tests in the worker container (real OCP, no mocks)
	$(CAD) run --rm --no-deps cad-worker pytest -q

test-api: api-build cad-build ## E2E: real api + ARQ cad-worker + Postgres + Redis + MinIO (no mocks)
	$(TEST) up -d --wait api
	$(TEST) up -d cad-worker
	$(TEST) run --rm api-tests pytest -q

web-check: install ## apps/web: tsc --noEmit, ESLint (0 warnings), Vitest (measure / datum / LUT), next build
	$(PNPM) --filter @revgate/web typecheck
	$(PNPM) --filter @revgate/web lint
	$(PNPM) --filter @revgate/web test
	$(PNPM) --filter @revgate/web build

test-web: stack web-check ## Browser E2E: Chromium (SwiftShader WebGL2) -> next start -> api + cad-worker + Postgres/Redis/MinIO (no mocks)
	$(PNPM) --filter @revgate/web exec playwright install chromium
	$(PNPM) --filter @revgate/web exec playwright test

test: test-cad test-api test-web ## All suites (CAD engine, API E2E, web + browser E2E)

glb-validate: install ## Run the Khronos glTF validator on GLBs kept by `make test-cad` (REVGATE_TEST_ARTIFACTS)
	$(CAD) run --rm --no-deps -e REVGATE_TEST_ARTIFACTS=/app/workers/cad/tests/.artifacts cad-worker pytest -q -k "golden or ingest"
	node scripts/validate-glb.mjs workers/cad/tests/.artifacts

clean: ## Remove local build artefacts (keeps docker volumes)
	rm -rf workers/cad/tests/.artifacts packages/contracts/py/.venv
