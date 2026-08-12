PYTHON ?= python
UVICORN ?= uvicorn

.PHONY: install install-dev run format lint check test test-unit test-integration test-e2e coverage coverage-unit docker-build docker-up docker-down docker-logs

install:
	$(PYTHON) -m pip install -r requirements.txt

install-dev:
	$(PYTHON) -m pip install -r requirements-dev.txt

run:
	$(PYTHON) -m uvicorn app.main:app --host 0.0.0.0 --port 8000 --no-access-log

format:
	$(PYTHON) -m ruff format app scripts tests
	$(PYTHON) -m ruff check --fix app scripts tests

lint:
	$(PYTHON) -m ruff format --check app scripts tests
	$(PYTHON) -m ruff check app scripts tests

check: lint coverage-unit test-integration test-e2e

test:
	$(PYTHON) -m pytest -v

test-unit:
	$(PYTHON) -m pytest tests/unit -v

test-integration:
	$(PYTHON) -m pytest tests/integration -v

test-e2e:
	$(PYTHON) -m pytest tests/e2e -v

coverage:
	$(PYTHON) -m coverage run -m pytest
	$(PYTHON) -m coverage report

coverage-unit:
	$(PYTHON) -m coverage erase
	$(PYTHON) -m coverage run -m pytest tests/unit
	$(PYTHON) -m coverage report --fail-under=90

docker-build:
	docker compose build

docker-up:
	docker compose up --build -d

docker-down:
	docker compose down

docker-logs:
	docker compose logs --follow app
