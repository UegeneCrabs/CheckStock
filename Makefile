PYTHON ?= python
UVICORN ?= uvicorn

.PHONY: install install-dev run scheduler format lint check docker-build docker-up docker-down docker-logs

install:
	$(PYTHON) -m pip install -r requirements.txt

install-dev:
	$(PYTHON) -m pip install -r requirements-dev.txt

run:
	$(PYTHON) -m uvicorn app.main:app --host 0.0.0.0 --port 8000 --no-access-log

scheduler:
	$(PYTHON) -m app.jobs.worker

format:
	$(PYTHON) -m ruff format app scripts run.py
	$(PYTHON) -m ruff check --fix app scripts run.py

lint:
	$(PYTHON) -m ruff format --check app scripts run.py
	$(PYTHON) -m ruff check app scripts run.py

check: lint

docker-build:
	docker compose build

docker-up:
	docker compose up --build -d

docker-down:
	docker compose down

docker-logs:
	docker compose logs --follow app scheduler
