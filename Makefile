# RevGate monorepo tasks (POSIX make: Linux / macOS / WSL with Docker integration / Git Bash + make).
#   make dev       Postgres 16 + Redis 7 + MinIO (3 buckets + CORS) up and healthy
#   make codegen   JSON Schema SSOT -> TypeScript + Pydantic v2 (+ verification)
#   make test-cad  build the linux/amd64 CAD worker and run the golden STEP suite inside it
# Override tools when needed, e.g. from WSL without Docker integration: make dev DOCKER=docker.exe

.DEFAULT_GOAL := help
SHELL := /bin/sh

DOCKER  ?= docker
PNPM    ?= pnpm
COMPOSE ?= $(DOCKER) compose -f infra/docker-compose.yml
CAD     := $(COMPOSE) --profile cad

.PHONY: help install dev down infra-status codegen contracts-check cad-build golden test-cad glb-validate clean

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

down: ## Stop local infra (volumes are kept)
	$(COMPOSE) down

infra-status: ## Show container health
	$(COMPOSE) ps -a

codegen: install ## Generate TS types + Pydantic models from packages/contracts/schemas and verify them
	$(PNPM) codegen

contracts-check: install ## Fail when generated contracts drift from the schemas (CI)
	$(PNPM) contracts:check

cad-build: ## Build the CAD worker image (linux/amd64, python 3.11, uv, cadquery-ocp)
	$(CAD) build cad-worker

golden: cad-build ## Regenerate the 5 golden STEP pairs (byte-reproducible)
	$(CAD) run --rm cad-worker python tests/gen_golden_steps.py

test-cad: cad-build ## Golden STEP regression + engine tests in the worker container (real OCP, no mocks)
	$(CAD) run --rm cad-worker pytest -q

glb-validate: install ## Run the Khronos glTF validator on GLBs kept by `make test-cad` (REVGATE_TEST_ARTIFACTS)
	$(CAD) run --rm -e REVGATE_TEST_ARTIFACTS=/app/workers/cad/tests/.artifacts cad-worker pytest -q -k "golden or ingest"
	node scripts/validate-glb.mjs workers/cad/tests/.artifacts

clean: ## Remove local build artefacts (keeps docker volumes)
	rm -rf workers/cad/tests/.artifacts packages/contracts/py/.venv
