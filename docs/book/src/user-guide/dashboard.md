# ダッシュボード

ShogiArena のダッシュボードは、実行中または保存済み run の状態をブラウザで確認するための UI です。

## 実行中に開く

run 設定で `dashboard.enabled: true` にすると、実行時に API サーバーが起動します。

```yaml
dashboard:
  enabled: true
  api_port: 8080
```

```bash
shogiarena run tournament tournament.yaml
```

ブラウザで `http://localhost:8080` を開きます。

## 閲覧できる範囲

ダッシュボードは、ローカルで 1 人が使うための画面で、認証機能はありません。
`dashboard.api_host` は `localhost`、`127.0.0.0/8`、`::1` のいずれかに限定され、`0.0.0.0` や LAN のアドレスでは起動しません。
外部マシンからの閲覧、リバースプロキシ経由の公開、multi-user 運用には対応していません。
ポートフォワーディングなどで外部へ公開しないでください。

## 保存済み run を開く

```bash
shogiarena dashboard serve --run-dir /path/to/run
```

`dashboard serve` は保存済みの結果を読み取り専用で開きます。
インスタンスの作成や変更、スケジュール操作、診断情報の保存はできません。

設定ファイルから最新 run を解決したい場合:

```bash
shogiarena dashboard serve --config tournament.yaml
```

ポートを変える場合:

```bash
shogiarena dashboard serve --run-dir /path/to/run --port 9090
```

## 表示内容

実行モードに応じて表示されるタブが切り替わります。

| モード | 主な用途 | 主な表示 |
| --- | --- | --- |
| `tournament` | 複数エンジン比較 | 順位表、対戦表、棋譜、エンジン設定 |
| `sprt` | 2 エンジンの統計検定 | LLR、判定状態、対局履歴 |
| `spsa` | パラメータチューニング | 更新履歴、パラメータ、収束状況 |
| `generate` | 自己対局の棋譜生成 | 生成数、出力ファイル、進捗 |
| `book` | 内蔵定跡の利用状況 | book fingerprint、勝率、先後別集計、out-of-book ply 分布 |

共通して、`Games` では保存済み対局を確認でき、`Live View` では進行中の対局盤面を見られます。

Bookタブは、`BookFile`などでエンジン自身に設定した定跡ファイルごとの成績を比較する画面です。
ShogiArenaが`rules.initial_positions`で選ぶ開始局面は対象外であり、そちらはOpeningsタブに表示します。
エンジン内蔵定跡を使わなかった run では、Book タブに分析対象がないことを表示します。

Bookタブのout-of-bookは、「実着手が指定book上の候補手集合に含まれたか」を後から観測する指標です。
エンジンが実際に book 由来で指したことを断定するものではありません。

Book タブの `Pairs` では、スケジュール上のペアごとに、定跡手順の一致状況、最初に異なる手数（first diff ply）、一致率（prefix match rate）、`measurement_status` を確認できます。
この手順は、記録された `book_hit` または明示的に有効にした定跡検索による候補手判定に基づきます。
エンジンが実際に定跡を使ったことを保証するものではありません。

`Live View` と対局詳細の時間情報では、持ち時間から差し引かれた `wall_time_ms` と、`think()` 呼び出しから `bestmove` 回収までの実測時間 `engine_wall_time_ms` を表示します。
エンジンの処理速度や wall NPS を比較するときは `engine_wall_time_ms` を使ってください。

## run ディレクトリ

ダッシュボードは run ディレクトリ内の成果物を読みます。
実行開始時にCLIが`Run directory: <absolute path>`を出力するため、このパスを保存済みrunの指定に使えます。
保存済みrunでは、`schedule.json`、`state.json`、`game.db`から対局一覧と完了数を読み取り専用で復元します。
代表的なファイルは次の通りです。

```text
run/
├── game.db
├── manifest.json
├── state.json
├── data/
└── records/
```

保存済み run を `shogiarena dashboard serve` で開く場合、現行 CLI は run ディレクトリ直下の `game.db` を必要とします。
自己対局生成などで dashboard を使う場合も、`game.db` を含む run ディレクトリを指定してください。
