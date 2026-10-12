# 開発への貢献

開発環境は`uv`で管理しています。
フロントエンドの依存関係は`npm`でインストールします。

## セットアップ

```bash
git clone https://github.com/nyoki-mtl/ShogiArena.git
cd ShogiArena
uv sync
npm ci
```

## 変更を確認する

主なコマンドは次のとおりです。
全コマンドは`make help`または[Makefile](https://github.com/nyoki-mtl/ShogiArena/blob/main/Makefile)で確認できます。

```bash
make format        # Pythonとフロントエンドのフォーマット
make lint          # Pythonのリント
make typecheck     # 型と依存関係の検査
make test          # Pythonのテスト
make frontend-test # フロントエンドのテスト
make ci            # 公開リポジトリのCIと同じ検査
```

`make check`はフォーマットと静的検査を実行します。
テストもまとめて実行する場合は`make check-full`を使ってください。

## ドキュメントを編集する

ドキュメントは`docs/book/src/`のMarkdownからmdBookで生成します。
`mdbook`をインストールし、次のコマンドで表示を確認してください。

```bash
make docs-build    # HTMLを生成
make docs-serve    # プレビューを開く
```

実装の配置は[プロジェクト構成](project-structure.md)にまとめています。
