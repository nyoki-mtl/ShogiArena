# ユーティリティ

トーナメント実行以外でよく使う CLI をまとめます。

## 設定確認

```bash
shogiarena config init
shogiarena config show
shogiarena config repo set yaneuraou \
  --path ~/repos/YaneuraOu \
  --url https://github.com/yaneurao/YaneuraOu.git \
  --build-config ~/.config/shogiarena/builds/yaneuraou.yaml
shogiarena config repo remove yaneuraou
```

`config init` は `settings.yaml` と標準ディレクトリを作ります。
設定なしでも動きますが、`{output_dir}` や `{engine_dir}`、artifact を使うなら初期化しておくと手間が減ります。

## 単一局面の解析

```bash
shogiarena run analyze engine.yaml startpos --nodes 100000
```

USI の `go` を使って 1 局面を探索します。
エンジン設定が意図通りかを確かめるときにも使えます。

## 詰み探索

```bash
shogiarena run mate engine.yaml startpos --ply-limit 5
```

`go mate` 対応エンジンで詰み探索を行います。
主な指定は `--ply-limit`、`--node-limit`、`--infinite` です。

## 棋譜生成

```bash
cp examples/configs/run/generate/example.yaml generate.yaml
shogiarena run generate generate.yaml
```

`run generate` は自己対局で棋譜や局面データを生成します。
設定は tournament 系 run と同じく、`engines`、`tournament`、`rules`、`records_output` を使います。

```yaml
records_output:
  format: sbinpack
  output_dir: "./data/records/generate"
  max_games_per_file: 500
  file_prefix: "selfplay"
```

生成状況は dashboard の Generate モードで確認できます。

## 結果集計

```bash
shogiarena results summary /path/to/run
shogiarena results summary /path/to/run --format json
shogiarena results summary /path/to/game.db --format csv
```

JSON 出力では `timing_metrics` に wall time field の意味が入ります。
engine throughput や wall NPS を比較するときは `engine_wall_time_ms` を、持ち時間に課金された時間を見たいときは `wall_time_ms` を使います。

マニフェストの provenance 検証:

```bash
shogiarena results verify-provenance /path/to/run
```

## 局面の再検索

保存済み run の対局から指定 ply を取り出し、エンジンで再検索します。

```bash
shogiarena replay-position --run-dir /path/to/run \
  --game-id g0001-abc \
  --ply 80 \
  --engine EngineA \
  --nodes 100000
```

USI transcript を保存している場合は、履歴再現付きの調査にも使えます。
