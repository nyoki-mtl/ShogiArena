# インストール

## 動作環境

- Python 3.11 以上
- USI 対応の将棋エンジン
- Linux / macOS / Windows

Windows では WSL2 上での利用を推奨します。ローカルで多数の対局を並列実行する場合は、エンジンのスレッド数と ShogiArena の並列数を合わせて調整してください。

## PyPI から使う

```bash
pip install shogiarena
shogiarena --help
```

標準の出力先やエンジン配置先を作る場合は初期設定を行います。

```bash
shogiarena config init
shogiarena config show
```

`config init` は必須ではありません。ただし、次の機能を使う場合は先に設定しておくとパス管理が分かりやすくなります。

- `{output_dir}` / `{engine_dir}` プレースホルダー
- artifact ベースのエンジン解決
- 共有キャッシュやリポジトリ設定

非対話で初期化する例:

```bash
shogiarena config init --non-interactive \
  --output-dir /path/to/output \
  --engine-dir /path/to/engines
```

## ソースから開発する

このリポジトリの開発では `uv` を使います。

```bash
git clone https://github.com/nyoki-mtl/ShogiArena.git
cd ShogiArena
uv sync --all-extras
uv run shogiarena --help
```

主な開発コマンド:

```bash
make help
make check
make test
make docs-build
```

## 次のステップ

- [クイックスタート](quick-start.md)
- [エンジン設定](../user-guide/engine-configuration.md)
