.PHONY: help install run test lint workflow demo up down logs import-workflow clean

PY ?= python3
VENV := .venv
BIN := $(VENV)/bin

help:
	@echo "make install          create the venv and install dependencies"
	@echo "make run              run the API on http://localhost:8000"
	@echo "make test             run the test suite"
	@echo "make workflow         regenerate n8n/workflow.lead-qualification.json"
	@echo "make demo             submit every sample lead and print the results"
	@echo "make up / down        start / stop the full docker compose stack"
	@echo "make import-workflow  load the workflow into the running n8n container"
	@echo "make logs             tail the compose logs"

install:
	$(PY) -m venv $(VENV)
	$(BIN)/pip install --upgrade pip
	$(BIN)/pip install -r requirements.txt
	@echo "done. next: cp .env.example .env && make run"

run:
	$(BIN)/uvicorn app.main:app --reload --host 0.0.0.0 --port 8000

test:
	$(BIN)/python -m pytest -q

workflow:
	$(PY) scripts/build_workflow.py

demo:
	$(BIN)/python scripts/demo.py

up:
	docker compose up --build -d
	@echo "form: http://localhost:3000   n8n: http://localhost:5678   api: http://localhost:8000/docs"
	@echo "now run: make import-workflow"

down:
	docker compose down

logs:
	docker compose logs -f --tail=80

import-workflow:
	docker compose exec n8n n8n import:workflow --input=/workflows/workflow.lead-qualification.json
	@echo "Imported. Open http://localhost:5678, open the workflow and switch it Active."

clean:
	rm -rf data/*.db data/*.db-wal data/*.db-shm .pytest_cache
	find . -name __pycache__ -type d -exec rm -rf {} +
