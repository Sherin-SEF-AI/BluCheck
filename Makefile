# BluCheck top-level operations.
# Most targets wrap scripts/ and infra/. Region and prefix come from the environment
# with sane defaults. Nothing here applies infrastructure without an explicit target.

SHELL := /bin/bash
AWS_REGION ?= ap-south-1
RESOURCE_PREFIX ?= blucheck
INFRA_DIR := infra

.DEFAULT_GOAL := help

.PHONY: help
help: ## Show this help
	@grep -hE '^[a-zA-Z0-9_.-]+:.*?## ' $(MAKEFILE_LIST) \
		| awk 'BEGIN {FS=":.*?## "}; {printf "  \033[36m%-18s\033[0m %s\n", $$1, $$2}'

.PHONY: infra-plan
infra-plan: ## terraform init + validate + plan (no apply, no spend)
	cd $(INFRA_DIR) && terraform init -input=false && terraform validate && terraform plan -out=tfplan

.PHONY: infra-up
infra-up: ## Provision the full AWS stack (terraform apply). Prints API URL, dashboard URL, buckets.
	cd $(INFRA_DIR) && terraform init -input=false && terraform apply -auto-approve
	@echo "----- outputs -----"
	cd $(INFRA_DIR) && terraform output

.PHONY: infra-down
infra-down: ## Destroy all AWS resources (terraform destroy + manual cleanup)
	./scripts/teardown.sh

.PHONY: deploy-backend
deploy-backend: ## Build + push API image and roll the ECS api service
	./scripts/deploy-backend.sh

.PHONY: deploy-worker
deploy-worker: ## Build + push worker image and roll the ECS worker service
	./scripts/deploy-worker.sh

.PHONY: dashboard-deploy
dashboard-deploy: ## Build the dashboard, sync to S3, invalidate CloudFront
	./scripts/deploy-dashboard.sh

.PHONY: deploy-all
deploy-all: deploy-backend deploy-worker dashboard-deploy ## Deploy backend, worker, and dashboard

.PHONY: seed
seed: ## Run migrations and create the admin user + sample vehicles
	./scripts/seed.sh

.PHONY: mobile-build
mobile-build: ## Trigger an EAS development build of the mobile app
	cd mobile && npx eas-cli build --profile development --platform all

.PHONY: status
status: ## Show ECS service, queue depth, and recent alarms
	@echo "== ECS services =="; \
	aws ecs describe-services --cluster $(RESOURCE_PREFIX)-cluster \
		--services $(RESOURCE_PREFIX)-api $(RESOURCE_PREFIX)-worker \
		--region $(AWS_REGION) \
		--query 'services[].{name:serviceName,desired:desiredCount,running:runningCount}' \
		--output table 2>/dev/null || echo "  (stack not deployed)"; \
	echo "== Extraction queue depth =="; \
	Q=$$(cd $(INFRA_DIR) && terraform output -raw extraction_queue_url 2>/dev/null); \
	if [ -n "$$Q" ]; then \
		aws sqs get-queue-attributes --queue-url "$$Q" \
			--attribute-names ApproximateNumberOfMessages ApproximateNumberOfMessagesNotVisible \
			--region $(AWS_REGION) --output table; \
	else echo "  (queue not provisioned)"; fi
