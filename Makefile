.PHONY: help up down build logs test verify verify-clean backup backup-dry restore scenarios-foundations scenarios-agents scenarios-api openapi

help: ## Show this help
	@awk 'BEGIN {FS = ":.*##"} /^[a-zA-Z_-]+:.*##/ {printf "  \033[36m%-15s\033[0m %s\n", $$1, $$2}' $(MAKEFILE_LIST)

up: ## Bring up the full stack (Postgres+AGE + workers + Langfuse)
	docker compose up -d twaky-pg
	docker compose up -d twaky-clickhouse twaky-redis twaky-seaweedfs
	docker compose up -d twaky-seaweedfs-init
	docker compose up -d twaky-langfuse-web
	docker compose up -d twaky-langfuse-worker twaky-ingest twaky-projector

down: ## Stop the stack (volumes preserved). Add ARGS='-v' to wipe.
	docker compose down $(ARGS)

build: ## Rebuild the twaky Python image
	docker compose build

logs: ## Tail logs of all twaky services
	docker compose logs -f --tail=50

test: ## Run pytest suite
	uv run pytest -q

verify: ## End-to-end verification of T1..T7
	@bash scripts/verify.sh

verify-clean: ## Wipe volumes and re-run verify from scratch
	docker compose down -v
	$(MAKE) up
	sleep 20
	$(MAKE) verify

backup: ## Dump PG + ClickHouse + SeaweedFS to /home/mmaudet/backups/twaky/<date>/
	@bash scripts/backup.sh

backup-dry: ## Show what backup would do (no side effects)
	@bash scripts/backup.sh --dry-run

restore: ## Restore all stores from DATE=YYYY-MM-DD (use FORCE=1 to skip prompt)
	@if [ -z "$(DATE)" ]; then echo "usage: make restore DATE=YYYY-MM-DD"; exit 2; fi
	@bash scripts/restore.sh $(DATE)

scenarios-foundations: ## Run the Foundations end-to-end scenario
	bash scripts/scenarios-foundations.sh

scenarios-agents: ## Run the Agents+Atlas end-to-end scenario
	bash scripts/scenarios-agents.sh

scenarios-api: ## Run the API end-to-end scenario
	bash scripts/scenarios-api.sh

openapi: ## Regenerate docs/api/openapi.yaml from the FastAPI app
	@mkdir -p docs/api
	@uv run python -m twaky.api.export_openapi > docs/api/openapi.yaml
	@echo "wrote docs/api/openapi.yaml"
