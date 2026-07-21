# インストール

## 動作環境

- Python 3.11 以上
- USI 対応の将棋エンジン
- Linux、macOS、Windows

Windows はネイティブ環境で動作します（WSL2 上でも利用できます）。

ローカルで多数の対局を並列実行する場合は、エンジンのスレッド数と ShogiArena の並列数を合わせて調整してください。

### CPU アーキテクチャ

将棋ドメインロジックは拡張モジュール `rsshogi` に依存します。
Windows x86_64、Linux x86_64、Linux arm64、macOS Intel、macOS Apple Silicon の
いずれにもビルド済み wheel が提供されるため、通常はソースビルドを必要としません。

x86_64 環境で将棋ロジックの処理速度を優先する場合は、AVX2 向けビルドへ差し替えられます。
両者は同じ `rsshogi` という import 名を提供するため、**同時にインストールはできません**。

```bash
pip install shogiarena
pip uninstall -y rsshogi
pip install rsshogi-avx2
```

注意点:

- AVX2 非対応の CPU や arm64 環境では `rsshogi-avx2` は使えません。
- 差し替え後は `shogiarena` の依存 `rsshogi` が未充足になり `pip check` が警告します。
  `pip install --upgrade shogiarena` などで portable 版が再インストールされるため、
  差し替えは更新のたびに適用し直してください。

差し替えが必要かどうかは、エンジン側の探索ではなく ShogiArena 側の棋譜処理が律速に
なっている場合にのみ検討してください。

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

`config init` は必須ではありません。
ただし、次の機能を使う場合は先に設定しておくとパス管理が分かりやすくなります。

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
uv sync
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

- [クイックスタート](quick-start.md)：エンジン 2 つで最小構成のトーナメントを動かす
- [エンジン設定](../user-guide/engine-configuration.md)：エンジン YAML の全項目
