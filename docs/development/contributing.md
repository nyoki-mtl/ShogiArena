# 開発への貢献

Shogi Arena の開発に貢献するためのガイドです。

## 開発環境のセットアップ

### 1. リポジトリのクローン

```bash
git clone https://github.com/nyoki-mtl/ShogiArena.git
cd ShogiArena
```

### 2. uv のインストール

```bash
# Linux / macOS
curl -LsSf https://astral.sh/uv/install.sh | sh

# Windows
powershell -c "irm https://astral.sh/uv/install.ps1 | iex"
```

### 3. 依存関係のインストール

```bash
uv sync --all-extras
```

### 4. pre-commit のセットアップ

```bash
pre-commit install
```

## コーディング規約

### Python コード

#### 型ヒント

すべての関数に型ヒントを付けます：

```python
# ✅ Good
def calculate_rating(current: float, opponent: float, result: float) -> float:
    return current + 16 * (result - expected_score(current, opponent))

# ❌ Bad
def calculate_rating(current, opponent, result):
    return current + 16 * (result - expected_score(current, opponent))
```

#### docstring

公開 API には docstring を付けます（日本語）：

```python
def update_ratings(engine1: str, engine2: str, result: str) -> dict[str, float]:
    """
    対局結果からレーティングを更新します。

    Args:
        engine1: エンジン1の名前
        engine2: エンジン2の名前
        result: engine1から見た結果（"win", "loss", "draw"）

    Returns:
        更新後のレーティング辞書
    """
    ...
```

#### 変数名

- 意味のある英語の単語を使用
- 略語は避ける（明確な場合を除く）

```python
# ✅ Good
engine_name = "YaneuraOu"
total_games = 100

# ❌ Bad
en = "YaneuraOu"
n = 100
```

#### ロガーメッセージ

ログメッセージは英語で記述：

```python
# ✅ Good
logger.info("Starting tournament with %d engines", len(engines))

# ❌ Bad
logger.info("トーナメントを開始します")
```

### フォーマットとリント

#### ruff による自動フォーマット

```bash
make format
```

#### ruff によるリントチェック

```bash
make lint
```

#### mypy による型チェック

```bash
make typecheck
```

#### すべてのチェックを実行

```bash
make check
```

## テスト

### テストの実行

```bash
# すべてのテストを実行
make test

# カバレッジ付き
make test-cov

# ユニットテストのみ
make test-unit

# 統合テストのみ
make test-integration
```

### テストの書き方

#### ユニットテスト

```python
import pytest
from shogiarena.arena.services.rating import RatingService

def test_rating_update_after_win():
    """勝利後のレーティング更新をテスト"""
    service = RatingService(initial_rating=1500, k_factor=16)

    new_ratings = service.update_ratings("EngineA", "EngineB", "win")

    assert new_ratings["EngineA"] > 1500  # EngineA のレーティングが上昇
    assert new_ratings["EngineB"] < 1500  # EngineB のレーティングが下降
```

#### 非同期テスト

```python
import pytest
from shogiarena.arena.engines.engine_factory import EngineFactory

@pytest.mark.asyncio
async def test_engine_lifecycle():
    """エンジンのライフサイクルをテスト"""
    engine = await EngineFactory.create_engine("tests/fixtures/mock_engine.yaml")
    await engine.start()

    assert engine.name == "MockEngine"

    await engine.close()
```

#### プロパティベーステスト

```python
from hypothesis import given, strategies as st
from shogiarena.arena.services.rating import RatingService

@given(
    rating1=st.floats(min_value=0, max_value=3000),
    rating2=st.floats(min_value=0, max_value=3000),
)
def test_rating_conservation(rating1: float, rating2: float):
    """レーティングの総和が保存されることをテスト"""
    service = RatingService(k_factor=16)
    service._ratings = {"A": rating1, "B": rating2}

    initial_sum = rating1 + rating2
    new_ratings = service.update_ratings("A", "B", "win")
    final_sum = new_ratings["A"] + new_ratings["B"]

    assert abs(final_sum - initial_sum) < 1e-6  # 誤差を許容
```

## ブランチ戦略

### ブランチ命名規則

- `feature/{feature-name}`: 新機能
- `fix/{bug-name}`: バグ修正
- `refactor/{description}`: リファクタリング
- `docs/{description}`: ドキュメント更新

### 例

```bash
git checkout -b feature/add-swiss-system
git checkout -b fix/rating-calculation-bug
git checkout -b docs/update-api-reference
```

## コミットメッセージ

### フォーマット

```
<type>: <subject>

<body>
```

### type の種類

- `feat`: 新機能
- `fix`: バグ修正
- `refactor`: リファクタリング
- `docs`: ドキュメント
- `test`: テスト追加・修正
- `chore`: その他の変更

### 例

```
feat: add Swiss system tournament scheduler

Implement a new scheduler that supports Swiss system tournaments.
This allows for more efficient tournament execution with large
numbers of participants.

Closes #123
```

## プルリクエスト

### プルリクエストを作成する前に

1. すべてのテストがパスすることを確認

```bash
make check
make test
```

2. コミットメッセージが規約に従っていることを確認

3. 変更内容を説明する README や docs を更新

### プルリクエストのテンプレート

```markdown
## 概要

この PR は何をするものか簡潔に説明してください。

## 変更内容

- 変更点1
- 変更点2
- 変更点3

## テスト方法

この変更をテストする方法を説明してください。

## チェックリスト

- [ ] テストがパスする
- [ ] ドキュメントを更新した
- [ ] コーディング規約に従っている
- [ ] 型チェックがパスする
```

### レビュープロセス

1. 自動テストが実行される（GitHub Actions）
2. コードレビューを受ける
3. 承認されたらマージ

## リリースプロセス

### バージョニング

Semantic Versioning (SemVer) を使用：

- `MAJOR.MINOR.PATCH`
- 例: `1.2.3`

### リリース手順

1. バージョン番号を更新（`pyproject.toml`）
2. CHANGELOG を更新
3. タグを作成

```bash
git tag -a v1.2.3 -m "Release version 1.2.3"
git push origin v1.2.3
```

## 質問・サポート

- **GitHub Issues**: バグ報告や機能リクエスト
- **GitHub Discussions**: 使い方の質問や議論

## 次のステップ

- **[プロジェクト構造](project-structure.md)** - コードベースの構造
- **[Architecture Overview](../technical/architecture.md)** - アーキテクチャの詳細
