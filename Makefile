.PHONY: install test lint format check

install:
	python -m pip install -e ".[dev]"

test:
	pytest

lint:
	ruff check .

format:
	ruff format .

check: lint
	ruff format --check .
	pytest
