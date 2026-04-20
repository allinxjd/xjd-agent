.PHONY: install dev test lint typecheck format clean docker-build docker-up

install:
	pip install -e .

dev:
	pip install -e ".[dev]"
	pre-commit install 2>/dev/null || true

test:
	python -m pytest tests/ -v

test-fast:
	python -m pytest tests/ -x -q

lint:
	ruff check .

lint-fix:
	ruff check --fix .

typecheck:
	mypy agent/ --ignore-missing-imports

format:
	ruff format .

clean:
	find . -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null || true
	find . -type d -name .pytest_cache -exec rm -rf {} + 2>/dev/null || true
	find . -type d -name "*.egg-info" -exec rm -rf {} + 2>/dev/null || true
	rm -rf build/ dist/ .mypy_cache/

docker-build:
	docker compose -f docker/docker-compose.yml build

docker-up:
	docker compose -f docker/docker-compose.yml up -d

docker-down:
	docker compose -f docker/docker-compose.yml down
