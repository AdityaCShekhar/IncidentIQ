UV ?= uv
COMPOSE ?= docker compose
NAME ?= slow_dependency
SCENARIO_ARGS ?=
EVIDENCE_ARGS ?=
SCENARIO_FILE = $(subst _,-,$(NAME))
EVIDENCE ?= docs/demos/slow-dependency-evidence.json

.PHONY: up down migrate logs ps sync dev test test-integration lint simulator-up scenario telemetry-config evidence dataset diagnose

up:
	$(COMPOSE) up --build --detach --wait --wait-timeout 120

down:
	$(COMPOSE) down

migrate:
	$(COMPOSE) run --rm migrate

logs:
	$(COMPOSE) logs --follow api db migrate telemetry-init checkout inventory otel-collector

ps:
	$(COMPOSE) ps --all

sync:
	$(UV) sync

dev:
	$(UV) run uvicorn incidentiq.main:app --reload --no-access-log

test:
	$(UV) run pytest -m "not integration"

test-integration:
	$(UV) run pytest -m integration

lint:
	$(UV) run ruff check .
	$(UV) run ruff format --check .

simulator-up:
	$(COMPOSE) up --build --detach --wait --wait-timeout 120 checkout prometheus

scenario:
	$(UV) run python scripts/run_scenario.py $(NAME) $(SCENARIO_ARGS)

telemetry-config:
	$(COMPOSE) run --rm --no-deps otel-collector validate --config=/etc/otelcol-contrib/config.yaml
	$(COMPOSE) run --rm --no-deps --entrypoint promtool prometheus check config /etc/prometheus/prometheus.yml

evidence:
	@$(COMPOSE) --profile tools build evidence 1>&2
	@$(COMPOSE) --profile tools run --rm --no-deps -T evidence --scenario-file /scenarios/$(SCENARIO_FILE).json --phase fault --service checkout --related-service inventory $(EVIDENCE_ARGS)

dataset:
	$(UV) run python scripts/validate_dataset.py

diagnose:
	@$(UV) run python scripts/diagnose.py --evidence "$(EVIDENCE)"
