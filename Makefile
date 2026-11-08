.PHONY: install lint typecheck test eval check run crm docker-build compose-config tf-validate
PY := .venv/bin/python
NICE := nice -n 10

install:
	python3 -m venv .venv
	.venv/bin/pip install -e ".[dev]"

lint:
	.venv/bin/ruff check .
	.venv/bin/ruff format --check .

typecheck:
	.venv/bin/mypy

test:
	$(NICE) .venv/bin/pytest --cov --cov-report=term-missing:skip-covered

eval:
	$(NICE) .venv/bin/aip eval run --min-pass-rate 0.9

check: lint typecheck test eval

# Run the API locally on SQLite. Override PORT if 8000 is taken.
PORT ?= 8000
run:
	.venv/bin/alembic upgrade head
	ADMIN_TOKEN=$${ADMIN_TOKEN:-dev-admin} .venv/bin/uvicorn --factory aip.api.app:app_factory --port $(PORT)

crm:
	.venv/bin/uvicorn mock_crm.app:app --port 9001

docker-build:
	docker build -t agent-integration-platform .

compose-config:
	docker compose config -q

tf-validate:
	cd infra && terraform init -backend=false && terraform validate
