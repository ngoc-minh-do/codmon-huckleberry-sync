.PHONY: install lint format format-check typecheck test audit check fix precommit hooks-update clean

install:
	uv sync
	uv run prek install

lint:
	uv run ruff check .

format:
	uv run ruff format .

format-check:
	uv run ruff format --check .

typecheck:
	uv run ty check --exit-zero-on-warning

test:
	uv run pytest || [ $$? -eq 5 ]

audit:
	uvx pip-audit

check: lint format-check typecheck test

fix:
	uv run ruff check --fix .
	uv run ruff format .

precommit:
	uv run prek run --all-files

hooks-update:
	uv run prek auto-update

clean:
	rm -rf .pytest_cache .ruff_cache
	find . -type d -name __pycache__ -prune -exec rm -rf {} +
