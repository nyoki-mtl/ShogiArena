.PHONY: help test test-cov test-unit test-property test-integration format format-check lint lint-fix python-import-cycle-check ts-import-cycle-check import-cycle-check type-safety-audit-check ty-check frontend-typecheck typecheck typing-audit typing-audit-baseline typing-audit-ci architecture-lint architecture-lint-strict architecture-lint-dynamic architecture-lint-dynamic-strict architecture-lint-final architecture-lint-final-strict convention-lint convention-lint-strict frontend-test check check-powershell-encoding check-full ci ci-public ci-develop ci-private clean check-distribution-artifacts check-examples check-public-export check-promote-release check-env docs docs-build docs-serve

# PATH 上の mdbook を使う。別の場所にある場合は `make docs MDBOOK=/path/to/mdbook` で上書きする。
MDBOOK ?= mdbook

# Windows では Windows PowerShell、それ以外では pwsh を使う。
ifeq ($(OS),Windows_NT)
POWERSHELL := powershell -NoProfile -ExecutionPolicy Bypass -File
else
POWERSHELL := pwsh -NoProfile -File
endif

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
	@echo "  format-check - コードフォーマット確認（非破壊）"
	@echo "  lint         - リントチェック（ruff check --fix）"
	@echo "  typecheck    - 型チェック（ty）"
	@echo "  typing-audit       - 型逸脱監査（差分計測）"
	@echo "  typing-audit-ci    - 型逸脱監査（回帰は失敗）"
	@echo "  typing-audit-baseline - ベースライン更新"
	@echo "  architecture-lint-final - final legacy profile で依存違反を計測"
	@echo "  architecture-lint-final-strict - final legacy profile で依存違反を失敗扱い"
	@echo "  architecture-lint-dynamic - shogiarena context/runtime 境界での動的import 迂回を検知"
	@echo "  architecture-lint-dynamic-strict - 動的 import 迂回があれば失敗扱い"
	@echo "  convention-lint - 命名/構成規約違反を計測（warn-only）"
	@echo "  convention-lint-strict - 命名/構成規約違反を失敗扱い"
	@echo "  check        - format 後に lint, convention-lint, typecheck, check-js を並列実行（テストなし）"
	@echo "  check-full   - check + test + frontend:test"
	@echo "  ci           - GitHub Actions の public CI と同等の検証"
	@echo "  ci-public    - public CI と同等の検証（ci の別名）"
	@echo "  ci-develop   - GitHub Actions の develop CI と同等の検証（typing-audit含む）"
	@echo "  ci-private   - develop CI と同等の検証（ci-develop の別名）"
	@echo "  check-distribution-artifacts - wheelのdashboard・型情報・第三者noticeを検証"
	@echo "  check-examples - 公開example設定をproduction CLIのdry-runで検証"
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

format-check:
	uv run ruff format . --check --config=pyproject.toml
	npm run frontend:format-check
	npm run frontend:check-static
	npm run frontend:check-html

lint:
	uv run ruff check . --config=pyproject.toml
	npm run frontend:lint

lint-fix:
	uv run ruff check . --fix --config=pyproject.toml

python-import-cycle-check:
	uv run python tools/detect_python_import_cycles.py

ts-import-cycle-check:
	uv run python tools/detect_ts_import_cycles.py

import-cycle-check:
	$(MAKE) -j2 python-import-cycle-check ts-import-cycle-check

type-safety-audit-check:
	uv run python tools/typing_audit.py \
		--root src \
		--baseline-json agent-docs/rules/typing-audit-baseline.json \
		--fail-on-regression \
		--summary

ty-check:
	uv run ty check src/

frontend-typecheck:
	npm run frontend:typecheck

typecheck:
	$(MAKE) --no-print-directory -j5 python-import-cycle-check ts-import-cycle-check architecture-lint-strict architecture-lint-dynamic-strict architecture-lint-final-strict
	$(MAKE) --no-print-directory -j3 type-safety-audit-check ty-check frontend-typecheck

architecture-lint:
	uv run python tools/detect_architecture_imports.py --root src/shogiarena

architecture-lint-strict:
	uv run python tools/detect_architecture_imports.py --root src/shogiarena --fail-on-violations

architecture-lint-dynamic:
	uv run python tools/detect_dynamic_import_cheats.py --root src

architecture-lint-dynamic-strict:
	uv run python tools/detect_dynamic_import_cheats.py --root src --fail-on-violations

architecture-lint-final:
	uv run python tools/detect_architecture_imports.py --root src/shogiarena --legacy-profile final

architecture-lint-final-strict:
	uv run python tools/detect_architecture_imports.py --root src/shogiarena --legacy-profile final --fail-on-violations

convention-lint:
	uv run python tools/detect_naming_conventions.py --root src --non-blocking-rule-id N008
	uv run python tools/detect_naming_conventions.py --root tests --non-blocking-rule-id N008
	uv run python tools/detect_naming_conventions.py --root tools --non-blocking-rule-id N008
	uv run python tools/detect_structure_conventions.py --root src

convention-lint-strict:
	uv run python tools/detect_naming_conventions.py --root src --non-blocking-rule-id N008 --fail-on-violations
	uv run python tools/detect_naming_conventions.py --root tests --non-blocking-rule-id N008 --fail-on-violations
	uv run python tools/detect_naming_conventions.py --root tools --non-blocking-rule-id N008 --fail-on-violations
	uv run python tools/detect_structure_conventions.py --root src --fail-on-violations

typing-audit:
	uv run python tools/typing_audit.py \
		--root src \
		--output agent-docs/reports/typing-audit-latest.md \
		--baseline-json agent-docs/rules/typing-audit-baseline.json

typing-audit-baseline:
	uv run python tools/typing_audit.py \
		--root src \
		--output agent-docs/reports/typing-audit-baseline.md \
		--baseline-json agent-docs/rules/typing-audit-baseline.json \
		--update-baseline

typing-audit-ci:
	uv run python tools/typing_audit.py \
		--root src \
		--output agent-docs/reports/typing-audit-latest.md \
		--baseline-json agent-docs/rules/typing-audit-baseline.json \
		--fail-on-regression

frontend-test:
	npm run frontend:test

# 統合チェック
check: format
	$(MAKE) --no-print-directory -j8 \
		lint convention-lint check-js check-powershell-encoding \
		python-import-cycle-check ts-import-cycle-check \
		architecture-lint-strict architecture-lint-dynamic-strict architecture-lint-final-strict \
		type-safety-audit-check ty-check frontend-typecheck

check-powershell-encoding:
	uv run python tools/check_powershell_encoding.py

check-full: check test frontend-test

check-js:
	npm run frontend:check-static
	npm run frontend:check-html

ci:
	uv run shogiarena --version
	$(MAKE) check-examples
	$(MAKE) check-powershell-encoding
	$(MAKE) format-check
	uv run ruff check . --config=pyproject.toml
	$(MAKE) import-cycle-check
	$(MAKE) architecture-lint-strict
	$(MAKE) architecture-lint-dynamic-strict
	$(MAKE) architecture-lint-final-strict
	uv run ty check src/
	npm run frontend:build
	uv run pytest
	npm run frontend:lint
	npm run frontend:typecheck
	npm run frontend:test
	uv build
	$(MAKE) check-distribution-artifacts

ci-public: ci

ci-develop:
	$(MAKE) typing-audit-ci
	$(MAKE) ci

ci-private: ci-develop

check-distribution-artifacts:
	uv run python tools/check_distribution_artifacts.py dist

check-examples:
	uv run python tools/check_example_configs.py

docs-build:
	$(MDBOOK) build docs/book

docs docs-serve:
	$(MDBOOK) serve docs/book --open

# public export に dev-only ファイルが混入しないことを検証する
check-public-export:
	$(POWERSHELL) scripts/check_public_export.ps1

# release commit 作成（dev -> main）が正しく動くことを検証する
check-promote-release:
	$(POWERSHELL) scripts/check_promote_release.ps1

# 開発に必要なツールが揃っているかを確認する
check-env:
	$(POWERSHELL) scripts/windows/check-env.ps1

# クリーンアップ（find/rm に依存せず Windows でも動く）
clean:
	uv run python tools/clean_caches.py
