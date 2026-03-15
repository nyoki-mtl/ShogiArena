# ShogiArena

[![CI](https://github.com/nyoki-mtl/ShogiArena/actions/workflows/public-ci.yml/badge.svg)](https://github.com/nyoki-mtl/ShogiArena/actions/workflows/public-ci.yml)
[![Docs](https://github.com/nyoki-mtl/ShogiArena/actions/workflows/public-docs.yml/badge.svg)](https://nyoki-mtl.github.io/ShogiArena/index.html)
[![PyPI](https://img.shields.io/pypi/v/shogiarena)](https://pypi.org/project/shogiarena/)
[![Python](https://img.shields.io/pypi/pyversions/shogiarena)](https://pypi.org/project/shogiarena/)
[![License](https://img.shields.io/github/license/nyoki-mtl/ShogiArena)](https://github.com/nyoki-mtl/ShogiArena/blob/main/LICENSE)

> [!NOTE]
> 本プロジェクトはまだ開発中です。公開 API は整理を進めている段階で、破壊的変更を含む更新が入ることがあります。変更履歴は [CHANGELOG](CHANGELOG.md) を参照してください。

**ドキュメント:** [https://nyoki-mtl.github.io/ShogiArena/index.html](https://nyoki-mtl.github.io/ShogiArena/index.html)
**English README:** [README.md](README.md)

---

ShogiArena は、将棋エンジンの対局実行・統計評価・ダッシュボード監視・エンジン自動化のためのプラットフォームです。

## 公開 Python API

正式にサポートしている import パス:

- `shogiarena.engine`
- `shogiarena.tournament`
- `shogiarena.cli`
- `shogiarena.composition`

実装本体は `shogiarena._core` 配下にあります。import 自体は可能ですが内部実装であり、後方互換性は保証しません。

## デモ

https://github.com/user-attachments/assets/1cdebe23-b1a9-4d8e-91c0-f56ca970b569

*リアルタイム更新とインタラクティブなダッシュボードによるトーナメント監視*

## インストール

```bash
pip install shogiarena
```

開発者向けのセットアップは [Contributing](https://nyoki-mtl.github.io/ShogiArena/development/contributing/) を参照してください。

## 設定（任意）

必須ではありませんが、以下の用途では初期設定を推奨します。

```bash
shogiarena config init
```

- artifact ベースのエンジン解決
- `{output_dir}` / `{engine_dir}` 等のプレースホルダー
- 共有キャッシュやリポジトリ設定

## クイック例

### Python から USI エンジンを使う

```python
import asyncio

from shogiarena.engine import UsiThinkRequest, create_engine


async def main() -> None:
    async with await create_engine("engine.yaml") as engine:
        result = await engine.think(
            sfen="startpos",
            request=UsiThinkRequest(movetime=5_000),
        )
        print(result.bestmove)


asyncio.run(main())
```

### Python からトーナメントを実行する

```python
import asyncio

from shogiarena.tournament import run_tournament


async def main() -> None:
    await run_tournament(
        "tournament.yaml",
        run_dir="runs/example",
    )


asyncio.run(main())
```

### CLI で実行する

```bash
shogiarena run tournament configs/run/tournament/example.yaml
shogiarena run sprt examples/configs/run/sprt/example.yaml
shogiarena run spsa examples/configs/run/spsa/example.yaml
```

`tournament` と `sprt` は公開 Python API と CLI の両方から利用可能です。`spsa` は現状 CLI 中心で、Python 向けの公開 API はまだ整備中です。

## ドキュメント

- [はじめに](https://nyoki-mtl.github.io/ShogiArena/getting-started/) - インストールと最初のステップ
- [ユーザーガイド](https://nyoki-mtl.github.io/ShogiArena/user-guide/) - トーナメント設定、SPSA、Python API
- [技術ドキュメント](https://nyoki-mtl.github.io/ShogiArena/technical/) - アーキテクチャ、USI、ダッシュボード内部
- [API リファレンス](https://nyoki-mtl.github.io/ShogiArena/api/) - 公開モジュールと補足リファレンス
- [Contributing](https://nyoki-mtl.github.io/ShogiArena/development/contributing/) - コントリビューション、ビルド、テスト

## ライセンス

MIT ライセンス。詳細は [LICENSE](LICENSE) を参照。
