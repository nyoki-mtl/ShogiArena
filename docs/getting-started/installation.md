# インストール

Shogi Arena のインストール方法を説明します。

## 動作環境

- **Python**: 3.10 以上
- **uv**: ソースから導入する場合に推奨
- **OS**: Linux, macOS, Windows (WSL2 推奨)

## PyPI からインストール（推奨）

最も簡単な導入方法です。

```bash
pip install shogiarena
```

インストール後は次のコマンドで動作確認できます：

```bash
shogiarena --help
```

## ソースからセットアップ（開発者向け）

開発や最新コードの検証をしたい場合は、リポジトリをクローンして `uv` で依存関係を同期します。

### uv のインストール

`uv` を使用します。

### Linux / macOS

```bash
curl -LsSf https://astral.sh/uv/install.sh | sh
```

### Windows (PowerShell)

```powershell
powershell -c "irm https://astral.sh/uv/install.ps1 | iex"
```

## Shogi Arena のセットアップ

### 1. リポジトリのクローン

```bash
git clone https://github.com/nyoki-mtl/ShogiArena.git
cd ShogiArena
```

### 2. 依存関係のインストール

```bash
uv sync --all-extras
```

### 3. 動作確認

```bash
shogiarena --help
```

### 4. 初期化

必要なディレクトリ構造と設定ファイルを作成します。デフォルトでは対話的に実行され、セットアップをガイドします。

```bash
# 対話的モード（デフォルト）
shogiarena config init
# または（互換エイリアス）
shogiarena init

# 非対話的モード（CI/自動化用）
shogiarena config init --non-interactive --output-dir /path/to/output --engine-dir /path/to/engines
```

## 次のステップ

- **[クイックスタート](quick-start.md)** - 最小構成で動かしてみる
