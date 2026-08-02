.PHONY: api-types

api-types: ## Regenerate src/lib/api-types.d.ts from ../docs/api/openapi.yaml
	@npx openapi-typescript ../docs/api/openapi.yaml -o src/lib/api-types.d.ts
	@echo "wrote frontend/src/lib/api-types.d.ts"
