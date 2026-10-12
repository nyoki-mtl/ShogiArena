# CSA 対局の観戦

`rsshogi-csa-bridge` は、floodgate などの CSA サーバーで USI エンジンを対局させ、run ごとに `{run_id}-events.jsonl` を出力します。
ShogiArena はこのログを読み、ブラウザや端末に対局状況を表示したり、棋譜を書き出したりします。
対局の進行やエンジンの操作は bridge が行います。

## 確認できる情報

CSA 対局では、サーバーが持ち時間、勝敗、ペアリングを決めます。
残り時間は、サーバーから返る消費時間（`,T`）をもとに再構成します。
相手側のエンジン出力、評価値、読み筋は取得できません。

盤面や棋譜に加えて、次の情報で対局の進行やエンジンの状態を確認できます。

- **alert**：`engine_dead` や `engine_restarted` などの警告
- **phase と滞在時間**：ペアリング待ちや対局中などの状態と、その状態になってからの時間
- **着手期限**：エンジンの応答期限までのカウントダウン
- **fallback**：エンジンが応答できず、保存された読み筋から指した手
- **時計**：サーバーの消費時間から求めた両者の残り時間

## 端末で状態を見る

```bash
shogiarena csa status --csa-log-dir /path/to/csa/logs
```

SSH 接続中など、ブラウザを開けない場合にも確認できます。

```text
run 1785844602  worker 0  bridge 0.1.0
  phase       playing  (12m 03s)
  game        rss-csa-test (b) vs nagisa_v4_p1351 (w)
              wdoor+floodgate-300-10F+rss-csa-test+nagisa_v4_p1351+20260804213004
  ply         148  (black to move)
  clock       black 1:03   white 3:04   (ledger)
  deadline    4.2s left (go)
  fallback    1 move(s), latest ply 78
  record      1W 1L 0D  (2 game(s))
  ponder      61/101 hit 60%  unresolved 0
  alerts      [error] engine_dead: engine process ended
              [warn] engine_restarted: restarting the engine (1 of 2)
  log         clean
```

| オプション | 意味 |
| --- | --- |
| `--csa-log-dir` | `{run_id}-events.jsonl` があるディレクトリ（必須） |
| `--run` | 1 つの run に絞る |
| `--follow` | 追記に追従して出し続ける |
| `--interval` | `--follow` の間隔（秒、既定 1.0） |

末尾の `log` 行には、ログの読み取り状態を表示します。
`seq` の欠落、不正なデータ、読めない行、未知のレコード種別、末尾の未完行をそれぞれ数えます。
`clean` でない場合は、表示情報に欠落や読み取り待ちがある可能性があります。

## ブラウザで観戦する

```bash
shogiarena dashboard watch --csa-log-dir /path/to/csa/logs
```

ダッシュボードには、次の 3 つのタブがあります。

- **Live View**：進行中の対局やペアリング待ちの run。盤面、棋譜、時計、評価値を表示します。
- **Games**：ログディレクトリ内の全対局。Game 名から盤面へ移動できます。
- **Monitor**：run ごとの状態。警告、期限超過、ログの異常などを強調します。

| オプション | 意味 |
| --- | --- |
| `--csa-log-dir` | イベントログのディレクトリ（必須） |
| `--port` | HTTP ポート（既定 8080、埋まっていれば次を探す） |
| `--out-run-dir` | 成果物の置き場（既定 `<output>/csa/<timestamp>`） |
| `--interval` | ポーリング間隔（秒、既定 1.0） |

`dashboard watch` は、追記されるイベントログを継続して読み取ります。
ログディレクトリが空でも起動でき、bridge の run がペアリング待ちなら、Live View に開始局面と `Waiting for pairing` を表示します。
同じログディレクトリにある複数の run を同時に観戦できます。

### 時計の見方

Live View では、サーバーの消費時間から求めた両者の残り時間を表示します。
手番側の時計は手元の経過時間で減り、着手イベントを受けるとサーバーの情報へ同期します。
表示する残り時間は、サーバー情報から求めた値を超えません。

時計や対局の詳細は Live View、run 全体の状態や警告、保存状況は Monitor で確認できます。

### 評価値グラフ

グラフには**自分の手番のみ**を表示します。
相手側の評価値は取得できません。

## 棋譜を書き出す

```bash
shogiarena csa export --csa-log-dir /path/to/csa/logs --out ./csa-records
```

全手を盤面に再生し、合法な指し手列であることを確認してから棋譜を書き出します。
再生に失敗した対局は出力せず、対局名、手番、理由を報告して、非ゼロの終了コードを返します。

| オプション | 意味 |
| --- | --- |
| `--csa-log-dir` | イベントログのディレクトリ（必須） |
| `--out` | `.csa` の出力先（既定 `./csa-export`）。ログディレクトリと同じにはできない |
| `--run` | 1 つの run に絞る |
| `--no-comments` | 評価値コメント行を出さない |

出力は CSA V3.0 で、先頭に `'CSA encoding=UTF-8` を付けます。
評価値は floodgate 形式のコメント行 `'** <cp> <pv...> #<nodes>` に記録します。
詰みを読んだ手のスコアも、bridge と同じ式で cp に換算します。

## 終わった対局を後から見る

`dashboard watch` は、終局した対局を `--out-run-dir` の `game.db` に保存します。
`watch` を止めたあとは、保存済みの結果として開けます。

```bash
shogiarena dashboard serve --run-dir <out-run-dir>
```

進行中の対局は `game.db` に保存されません。
`watch` を起動し直すと、イベントログから表示を再構成します。

## 制約

- 読み取るのはイベントログです。`wire.log` と `engine.log` は対象外です。
- 相手側の評価値、読み筋、エンジン出力は取得できません。
- 進行中の対局は `game.db` に保存されません。
- `watch` が保存中のディレクトリを、同時に `serve` で開くことはできません。実行中は `watch` の画面を使ってください。
