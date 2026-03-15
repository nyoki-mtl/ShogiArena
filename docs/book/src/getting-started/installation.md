# インストール

ShogiArena の導入方法を説明します。

## 動作環境

- Python 3.11 以上
- Linux / macOS / Windows（Windows は WSL2 推奨）
- USI 対応の将棋エンジン

## PyPI からインストール

```bash
pip install shogiarena
```

動作確認:

```bash
shogiarena --help
```

## ソースからセットアップ

開発や最新コードの検証をしたい場合は、`uv` で依存関係を同期します。

### 1. `uv` のインストール

Linux / macOS:

```bash
curl -LsSf https://astral.sh/uv/install.sh | sh
```

Windows (PowerShell):

```powershell
powershell -c "irm https://astral.sh/uv/install.ps1 | iex"
```

### 2. リポジトリの取得

```bash
git clone https://github.com/nyoki-mtl/ShogiArena.git
cd ShogiArena
```

### 3. 依存関係の同期

```bash
uv sync --all-extras
```

### 4. CLI の確認

```bash
uv run shogiarena --help
```

### 5. 初期設定

設定ファイルと標準出力先を用意したい場合は `config init` を実行します。

```bash
# 対話モード
shogiarena config init

# 非対話モード
shogiarena config init --non-interactive \
  --output-dir /path/to/output \
  --engine-dir /path/to/engines
```

`config init` をしなくても ShogiArena 自体は動きますが、`{output_dir}` / `{engine_dir}` プレースホルダーや artifact ベースのエンジンを使うなら先に設定しておくほうが分かりやすいです。

## 次のステップ

- [クイックスタート](quick-start.md)
