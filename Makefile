.PHONY: install test test-quick lint format typecheck clean all docker-build docker-run migrate

all: lint typecheck test

install:
	pip install -e ".[dev]"

test:
	pytest tests/ -v --cov=aumos_benchmark_suite --cov-report=term-missing

test-quick:
	pytest tests/ -x -q --no-header

lint:
	ruff check src/ tests/
	ruff format --check src/ tests/

format:
	ruff format src/ tests/
	ruff check --fix src/ tests/

typecheck:
	mypy src/aumos_benchmark_suite/

clean:
	find . -type d -name __pycache__ -exec rm -rf {} +
	find . -type d -name .pytest_cache -exec rm -rf {} +
	find . -type d -name .mypy_cache -exec rm -rf {} +
	rm -rf dist/ build/ *.egg-info

docker-build:
	docker build -t aumos/benchmark-suite:dev .

docker-run:
	docker compose -f docker-compose.dev.yml up -d

docker-logs:
	docker compose -f docker-compose.dev.yml logs -f app

migrate:
	alembic -c src/aumos_benchmark_suite/migrations/alembic.ini upgrade head

migrate-create:
	@read -p "Migration name: " name; \
	alembic -c src/aumos_benchmark_suite/migrations/alembic.ini revision --autogenerate -m "bnk_$$name"
