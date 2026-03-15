# 開発への貢献

> [!NOTE]
> このページには contributor 向けの内部 import 例が含まれます。外部利用者向けの supported import path は `shogiarena.engine` / `shogiarena.tournament` / `shogiarena.cli` / `shogiarena.composition` です。

ShogiArena の開発に参加するための最小ガイドです。

## セットアップ

```bash
git clone https://github.com/nyoki-mtl/ShogiArena.git
cd ShogiArena
uv sync --all-extras
```

必要に応じて frontend 依存も入れます。

```bash
npm ci
```

## よく使うコマンド

```bash
make format        # コードフォーマット（ruff format）
make lint          # リントチェック（ruff check --fix）
make typecheck     # 型チェック（ty）
make check         # format, lint, convention-lint, typecheck, check-js を実行（テストは含まない）
make test          # 全テスト実行
make check-full    # check + test + frontend-test
make ci            # public GitHub CI と同等の検証
make ci-develop    # develop GitHub CI と同等の検証（typing-audit含む）
make check-all     # check-full + pre-commit --all-files
```

ドキュメントは現在 `mdBook` です。

```bash
make docs-build    # 静的ドキュメントのビルド
make docs-serve    # ローカルサーバーで自動リロード付きプレビュー（ブラウザが開きます）
```

## CI/CD と公開フロー

- private の `ShogiArena-dev` では `develop-ci.yml` が `make ci-develop` を実行します。
- public の `ShogiArena` では `public-ci.yml` が `make ci` を実行し、`public-docs.yml` が docs build と GitHub Pages deploy、`public-release.yml` が tag push (`v*`) を契機に GitHub Release と PyPI publish を実行します。
- `typing-audit-ci` は `agent-docs/` の baseline に依存するため、develop 側 CI のみで実行します。

public への export は補助スクリプトで行えます。

```bash
scripts/export_public_snapshot.sh --notes-file release-notes/v0.8.0.txt v0.8.0
git push public HEAD:main
```

このスクリプトは `develop/main` を public snapshot に重ねつつ、`agent-docs/` や `AGENTS.md` などの dev-only ファイルに加えて、`dist/` や `site/` のようなローカル生成物も除外し、public 側に不要な `develop-ci.yml` も落とします。

## いまの構造

実装正本:

- `src/shogiarena/_core/contexts`
- `src/shogiarena/_core/platform`
- `src/shogiarena/_core/interfaces`
- `src/shogiarena/_core/shared/kernel`

利用者向け facade:

- `src/shogiarena/engine.py`
- `src/shogiarena/tournament.py`
- `src/shogiarena/cli.py`
- `src/shogiarena/composition.py`

## 変更時の基本方針

- 公開 API の説明では `shogiarena.engine` / `shogiarena.tournament` を優先する
- `_core` の deep import は contributor / 実装用途に限定する
- wiring は composition root に閉じ込める
- `apply_patch` ベースで編集し、破壊的変更を避けるための互換レイヤは原則作らない

## テスト例

### public API のテスト

```python
import shogiarena.engine
import shogiarena.tournament


def test_public_engine_exports() -> None:
    exported = set(shogiarena.engine.__all__)
    assert "AsyncUsiEngine" in exported
    assert "create_engine" in exported
```

### internal service のユニットテスト

```python
from shogiarena._core.shared.kernel.statistics.btd_rating import BTDEstimator


def test_btd_estimation_runs() -> None:
    estimator = BTDEstimator()
    result = estimator.estimate(
        games=[],
        engine_names=["engine-a", "engine-b"],
    )
    assert result.anchor in {"engine-a", "engine-b"}
```

### 非同期 engine テスト

```python
import pytest

from shogiarena.engine import UsiThinkRequest, create_engine_from_mapping


@pytest.mark.asyncio
async def test_engine_lifecycle() -> None:
    async with await create_engine_from_mapping(
        {
            "name": "mock",
            "engine_path": "tests/fixtures/mock_engine.py",
            "options": {},
        }
    ) as engine:
        result = await engine.think(
            sfen="startpos",
            request=UsiThinkRequest(movetime=100),
        )
        assert result.bestmove is not None
```

## コミット

Conventional Commits を使います。

例:

- `refactor: move internals under _core`
- `docs: align public api docs with facades`
- `test: add public surface regression coverage`

## レビュー観点

特に次を見ます。

- 公開 API と internal API の境界が崩れていないか
- `_core` への移動で import 方向が壊れていないか
- facade が wiring を持ち込みすぎていないか
- docs が public surface と一致しているか

## 補足

このリポジトリはまだ破壊的変更を許容しています。したがって、互換レイヤを残すよりも「現在の正本を明確にする」ことを優先します。
