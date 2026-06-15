# エンジン設定

ShogiArena は USI エンジンを YAML で定義します。トーナメント設定の `engines` からこの YAML を参照するか、`artifact` を直接指定します。

## ローカルエンジン

```yaml
name: "Local Engine"
engine_path: "/path/to/engine"
working_directory: "/path/to"
engine_args:
  - "--usi"
environment:
  OMP_NUM_THREADS: "2"
options:
  Threads: 2
  USI_Hash: 256
go_options:
  byoyomi: 1000
enable_early_ponder: false
```

主なフィールド:

| フィールド | 説明 |
| --- | --- |
| `name` | 表示名。省略時は `engine_path` などから派生 |
| `engine_path` | USI エンジン実行ファイル |
| `working_directory` | エンジンの作業ディレクトリ |
| `engine_args` | エンジン起動時の引数 |
| `environment` | エンジンプロセスへ渡す環境変数 |
| `options` | USI `setoption` として送る値 |
| `go_options` | `go` コマンドへ渡す既定値 |
| `enable_early_ponder` | ponderhit を早めに送る実験的オプション |

`engine_path`、`working_directory`、`options` 内のパス値には相対パスや `{engine_dir}` を使えます。

## 内蔵定跡

YaneuraOu 系エンジンの `BookDir` / `BookFile` は、エンジン内蔵定跡として扱います。ShogiArena は指し手を book から選ばず、USI option をエンジンへ渡したうえで、実体ファイルの検証と provenance 記録を行います。

```yaml
options:
  USI_OwnBook: true
  BookDir: "./book"
  BookFile: "user_book1.db"
  BookOnTheFly: true
```

`USI_OwnBook` が明示的に `false` でなく、`BookFile` が `no_book` でない場合、`BookDir + BookFile` の実体パスを起動前に検証します。大きな YANEURAOU-DB2016 形式の book では `BookOnTheFly: true` を使う運用を推奨します。

実力比較や SPSA では、内蔵定跡をエンジンごとに変えると「エンジンではなく定跡」を比較することになります。既定のサンプル overlay は `BookFile: no_book` とし、共有開始局面集は `rules.initial_positions` で指定します。

## artifact エンジン

ビルド済み成果物やリポジトリ定義からエンジンを解決する場合は `artifact` を使います。

```yaml
name: "YO-mainline"
artifact: "YaneuraOu/9f89431a"
build_options:
  target_cpu: ZEN3
  edition: YANEURAOU_ENGINE_NNUE_HALFKP_512X2_8_64
options:
  Threads: 4
  USI_Hash: 2048
```

`artifact` と `engine_path` は同時に指定できません。`artifact` を使う場合、通常は `build_options.target_cpu` が必要です。

## トーナメント側で上書きする

同じエンジン YAML を使いながら、トーナメントごとに名前や USI オプションを変えられます。

```yaml
engines:
  - engine_path: "engines/yaneuraou.yaml"
    name: "YaneuraOu_Strong"
    options:
      Threads: 4
      USI_Hash: 1024
  - engine_path: "engines/yaneuraou.yaml"
    name: "YaneuraOu_Light"
    options:
      Threads: 1
      USI_Hash: 128
```

`options_overlays` を使うと、USI オプション上書き用 YAML を段階的にマージできます。

```yaml
engines:
  - artifact: "YaneuraOu/9f89431a"
    build_options:
      target_cpu: ZEN3
    options_overlays:
      - "examples/configs/resources/engines/overlays/YaneuraOu.yaml"
    options:
      Threads: 4
```

## 確認する

単体エンジンの起動確認:

```bash
shogiarena run analyze engine.yaml startpos --nodes 100000
shogiarena run mate engine.yaml startpos --ply-limit 5
```

トーナメント設定込みの検証:

```bash
shogiarena run tournament tournament.yaml --dry-run
```

## よくある問題

- 実行ファイルに権限がない: `chmod +x /path/to/engine`
- USI オプション名が違う: エンジンを手動起動して `usi` の出力を確認する
- 評価関数や定跡の相対パスがずれる: `working_directory` と `path-preflight` を確認する
- artifact が解決できない: `shogiarena config repo ...` と `settings.yaml` のリポジトリ定義を確認する

## サンプル

- `examples/configs/resources/engines/local_example.yaml`
- `examples/configs/resources/engines/artifact_example.yaml`
- `examples/configs/resources/engines/overlays/`
