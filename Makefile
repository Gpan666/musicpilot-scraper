.PHONY: install dev test lint check run

install:
	uv venv .venv
	uv pip install -e ".[dev]"

dev:
	uvicorn musicpilot.app:create_app --factory --reload --host 127.0.0.1 --port 8000

run:
	uvicorn musicpilot.app:create_app --factory --host 0.0.0.0 --port 8000

test:
	python -m pytest tests

lint:
	python -m ruff check musicpilot tests

check: lint test
