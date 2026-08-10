# CSA 対局の観戦

`rsshogi-csa-bridge` は CSA プロトコルサーバー（floodgate、世界コンピュータ将棋選手権、
電竜戦）で USI エンジンを対局させ、1 run につき `{run_id}-events.jsonl` を書く。
ShogiArena はこのログを読んで、対局を観戦し、棋譜として書き出す。

ShogiArena は**読み手**である。対局そのものには一切関与しない。

## 何を見るためのものか

CSA 対局は ShogiArena が自分で回す対局と 2 つの点で違う。

**サーバーが権威を持つ。** 時計も勝敗もペアリングも remote が決める。
こちらの時計はサーバーの `,T` エコーから再構成した台帳であり、推定ではない。

**相手が黒箱である。** 相手のエンジン出力も評価値も読み筋も無い。

その結果、盤面と棋譜だけでは足りない。大会中に本当に知りたいのは
「おかしくなっていないか」であり、それを答えるのが以下の情報である。

1. **alert** — `engine_dead` や `engine_restarted`。次のペアリングの前に見るべきもの
2. **phase と滞在時間** — `waiting_pairing` 40 分は正常、`agreed` 100 秒は異常
3. **着手期限のカウントダウン** — `engine_hang` が出る前に赤くなる唯一の数字
4. **fallback 出所** — エンジンが答えられず読み筋で指している合図
5. **両者の台帳時計**

## `shogiarena csa status` — 端末で数行だけ見る

```bash
shogiarena csa status --csa-log-dir /path/to/csa/logs
```

ブラウザを開けない状況（ssh 越しなど）のための手段である。

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

末尾の `log` 行はログ自体の健全性を出す。
`seq` の欠落・壊れた payload・読めない行・未知のレコード種別・末尾の未完行を
それぞれ別に数えており、`clean` でなければ表示の信頼度が落ちていることを意味する。

## `shogiarena dashboard watch` — ブラウザで見る

```bash
shogiarena dashboard watch --csa-log-dir /path/to/csa/logs
```

Tournament/SPSAと同じダッシュボードを、CSAの責務に合わせた3つのタブで表示する。

- **Live View** — 進行中の対局、またはペアリング待ちのrun。盤面・棋譜・時計・評価値を表示する
- **Games** — ログディレクトリ内で見つかった全対局。1局1行で、Game名から盤面へ移動できる
- **Monitor** — 1run 1行の運用状態。正常時は静かにし、alert・期限超過・ログ異常などを強調する

表示ラベルと表の列順はTournament/SPSAと同じ英語UIへ揃えている。

| オプション | 意味 |
| --- | --- |
| `--csa-log-dir` | イベントログのディレクトリ（必須） |
| `--port` | HTTP ポート（既定 8080、埋まっていれば次を探す） |
| `--out-run-dir` | 成果物の置き場（既定 `<output>/csa/<timestamp>`） |
| `--interval` | ポーリング間隔（秒、既定 1.0） |

`serve` ではなく `watch` である理由は契約が正反対だからである。
`dashboard serve` は「もう誰も書いていないアーカイブ」を開く。
CSA 追跡はその逆で、「今まさに追記されている」ログを追う。

ログディレクトリが空でもページは起動する。bridge runを検出してペアリング待ちなら、
Live Viewに開始局面の盤と`Waiting for pairing`を表示する。
floodgate のペアリングは毎時 :00 と :30 なので、これが 1 時間の大半である。

複数の bridge run を同時に追える。同じログディレクトリへ複数processが書いても、
ファイル名のrun IDとserver game IDの組で対局を区別する。

### 時計の見方

Live Viewの対局カードは、サーバーの`,T`エコーから再構成した台帳値を正本として
両者の残り時間を表示する。手番側だけを手元の経過時間で減らし、着手イベントを受けると
新しい台帳値へ同期する。推定は台帳値を超えない。

Monitorには同じ時計を重複表示しない。時計を含む対局の詳細はLive View、
run全体の生存・警告・保存状態はMonitorで確認する。

### 評価値グラフ

**自分の手番のみ**が描かれる。相手のエンジンは remote な黒箱であり、
評価値は取得できない。これは不具合ではない。

## `shogiarena csa export` — 棋譜として書き出す

```bash
shogiarena csa export --csa-log-dir /path/to/csa/logs --out ./csa-records
```

**この機能の主目的は棋譜ではなく監査である。**

全手を `rsshogi` の盤面に再生し直してから書く。
再生できたということは、ログの指し手列が合法な対局として再構成できたということであり、
bridge の出力と ShogiArena の解釈が食い違っていないことの確認になる。

再生に失敗した対局は**書かない**。対局名・手番・理由を報告し、
exit code は非ゼロになる。検証していない棋譜を黙って書く経路は無い。

| オプション | 意味 |
| --- | --- |
| `--csa-log-dir` | イベントログのディレクトリ（必須） |
| `--out` | `.csa` の出力先（既定 `./csa-export`）。ログディレクトリと同じにはできない |
| `--run` | 1 つの run に絞る |
| `--no-comments` | 評価値コメント行を出さない |

出力は CSA V3.0（先頭 `'CSA encoding=UTF-8`）。
評価値は floodgate 形式のコメント行 `'** <cp> <pv...> #<nodes>` として載る。
詰みを読んだ手のスコアは bridge と同じ式で cp スケールへ写す。

## 終わった対局を後から見る

`dashboard watch` は終局した対局を `--out-run-dir` の `game.db` に書く。
`watch` を止めたあとは通常のアーカイブとして開ける。

```bash
shogiarena dashboard serve --run-dir <out-run-dir>
```

進行中の対局は `game.db` に無い。イベントログが正本なので、
必要なら `watch` を起動し直せば畳み直される。

## 制約

- `wire.log` と `engine.log` は読まない
- 相手側の評価値・読み筋・エンジン出力は存在しない
- 進行中の対局は `game.db` に無い
- `watch` が書いているディレクトリを同時に `serve` することはできない
  （`watch` 自身がライブサーバーである）
