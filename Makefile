.PHONY: help test test-cov test-unit test-property test-integration format lint lint-fix typecheck frontend-test check check-all clean docs docs-build docs-serve

# デフォルトターゲット
help:
	@echo "Usage: make [target]"
	@echo ""
	@echo "Available targets:"
	@echo "  sync         - 全依存関係を同期"
	@echo "  test         - 全テスト実行（単体・プロパティ・統合）"
	@echo "  test-cov     - カバレッジ付きテスト実行"
	@echo "  test-unit    - 単体テストのみ実行"
	@echo "  test-property - プロパティベーステストのみ実行"
	@echo "  test-integration - 統合テストのみ実行"
	@echo "  format       - コードフォーマット（ruff format）"
	@echo "  lint         - リントチェック（ruff check --fix）"
	@echo "  typecheck    - 型チェック（ty）"
	@echo "  check        - format, lint, typecheck, test, frontend:test を順番に実行"
	@echo "  check-all    - checkの内容 + pre-commitで全ファイルをチェック"
	@echo "  clean        - キャッシュファイルの削除"

sync:
	uv sync --all-extras

# テスト関連
test:
	uv run pytest

test-cov:
	uv run pytest --cov=src --cov-report=html --cov-report=term

test-unit:
	uv run pytest tests/unit/ -v

test-property:
	uv run pytest tests/property/ -v

test-integration:
	uv run pytest tests/integration/ -v

# コード品質チェック
format:
	uv run ruff format . --config=pyproject.toml
	npm run frontend:format
	npm run frontend:format-static
	npm run frontend:format-html

lint:
	uv run ruff check . --config=pyproject.toml
	npm run frontend:lint

lint-fix:
	uv run ruff check . --fix --config=pyproject.toml

typecheck:
	uv run ty check src/
	npm run frontend:typecheck

frontend-test:
	npm run frontend:test

# 統合チェック
check: format lint typecheck test frontend-test check-js

check-js:
	npm run frontend:check-static
	npm run frontend:check-html

check-all: check
	uv run pre-commit run --all-files

docs-build:
	mkdocs build --strict

docs docs-serve:
	mkdocs serve --dev-addr 127.0.0.1:8000

# クリーンアップ
clean:
	find . -type d -name "__pycache__" -exec rm -rf {} +
	find . -type f -name "*.pyc" -delete
	find . -type d -name ".pytest_cache" -exec rm -rf {} +
	find . -type d -name ".mypy_cache" -exec rm -rf {} +
	find . -type d -name ".ty" -exec rm -rf {} +
	find . -type d -name ".ruff_cache" -exec rm -rf {} +
	find . -type d -name "htmlcov" -exec rm -rf {} +
	find . -type f -name ".coverage" -delete
