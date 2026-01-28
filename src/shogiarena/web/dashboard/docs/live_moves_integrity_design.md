# Live Dashboard: moves整合性崩壊の課題と設計案

作成日: 2025-12-30  
対象: `src/shogiarena/web/dashboard/`（WS配信・snapshot解決）およびフロント `src/shogiarena/web/dashboard/frontend/`（Live表示）

このドキュメントは、Live Dashboard における「棋譜（moves）と局面（SFEN）の不整合」「missing/illegal move の発生」「放置での劣化（カクつき・崩壊）」について、
現状の問題・難しさの原因・観測されたログ症状・暫定対策・恒久設計案（複数）を整理したもの。

目的は「他エージェント/他開発者と議論できる共通の設計資料」を作ること。

---

## 0. 運用前提 / 非目的

**運用前提**
- `gid` は **再利用しない**（やり直しは新しい `gid` を割り当てる）。
- **巻き戻し（history_rev）は現時点で非対応**。導入は問題発生時に再検討する。
- Live View の目的は **最新局面の追従**であり、厳密な全履歴の復元は必須ではない。

**非目的**
- 途中局面からの厳密な再構築や履歴の完全性保証。

---

## 1. 何が起きているか（現象）

### 1.1 ユーザー体感
- 対局が進むと、盤面と棋譜の対応がズレて「壊れて見える」。
- しばらく放置するとカクつきが増え、ズレが悪化する。
- ときどき「カード名（Worker #N: gid）が初期のまま更新されない」ケースがある。
- 「棋譜は流れているのに局面が自動追跡されない」状態が発生する。

### 1.2 Debugログ（`legalMoveCheck.ts`）で観測される症状
フロント側で tsshogi を使った合法手チェックを常時ON（fail-fastの観測目的）にしたところ、
次の2種の症状が出る:

- `missing move at ply`  
  - moves配列に「穴」があり、連続して再生できない（contiguousLen < currentPly / movesLen）。
  - この状態になるとクライアントは「連続して再生できる最後」より先に進めず、追跡が止まる。
- `illegal move`  
  - 穴ではなく、あるplyのUSIがその局面から合法でない（順序ズレ、誤った上書き、別ゲームの混入等が疑われる）。

例（missingの典型）:
- `contiguousLen=69` なのに `movesLen=152`、`ply=70` が欠けているため、以後が再生不能。

---

## 2. なぜ難しいのか（構造的な難しさ）

Live更新は本質的に「分散ログの合流・欠損復旧」の問題になる。
そのうえで **棋譜（moves）は“加算ログ”** であり、時計・評価・サマリなどの“状態”とは性質が違う。

### 2.1 movesは「1件欠けると全部崩れる」データ
- eval/時計/メタは「最新が分かればよい」ので coalesce（最新だけ残す）や間引きが成立しやすい。
- moves は「順序付きの全件が必要」なので、coalesceや間引きが成立しない（中間が欠けると以降が再生不能）。
- ただし **「着手に紐づく確定値（analysis_final の eval/depth/nodes/time_ms など）」は moves と同じく順序依存**なので、state 系として扱うと履歴が破綻しやすい。
  - 最新表示だけが必要な評価値は `analysis.diff` に流し、履歴に残す確定値は `moves.diff` 側に載せる。

### 2.2 配信・復旧経路が複数あり非同期で混ざる
- 通常diff（リアルタイム）・assignment変更・subscribe bootstrap・request_snapshot replay・snapshot resolver が同時に走る。
- 「どれが最新基準か」を一貫して定義しないと、古いsnapshotの上書きや欠損が起こる。

### 2.3 高負荷時のドロップ/圧縮が、movesに致命的
- サーバのWSハブ（`ws_server.py`）は、クライアントqueueが満杯になったときにキュー圧縮・coalesceを行う。
- 現状の実装では `live.game.*.(diff|snapshot)` が coalesce 対象に含まれている（topic単位で最新だけ残す）。
- これは状態系（時計/評価）には有効だが、moves（逐次ログ）には致命的になり得る。

---

## 3. 現状アーキテクチャの整理（概念図）

### 3.1 サーバ側（概略）
- LiveHub（`ws_server.py`）:
  - topicごとに `seq` を単調増加
  - topicごとに ring buffer を保持（replay用）
  - クライアントqueue満杯時に優先度に応じて drop/compaction/coalesce を実行
- snapshot_resolver（`api_server.py`）:
  - `request_snapshot(topic, fromSeq)` の際に、必要に応じてスナップショットを返す
  - `live.game.<gid>.snapshot` などを返す

### 3.2 フロント側（概略）
- WS受信（`ws.ts`）→ merge worker（`liveMergeWorker.ts`）→ main（`eventsController.ts`）→ state更新 → 描画
- merge workerは、topic seqギャップ検知で `request_snapshot` を出す。
- main threadはrAFでVMを合流する（頻繁なpostMessage削減）。
- `legalMoveCheck.ts` はstate上の snapshot.moves を tsshogi で検証し、missing/illegal をログに出す。

#### 重要: movesの整合性責務の一元化（2025-12-31時点の方針）
Live の壊れ方（穴→連鎖スキップ）を根絶するため、**moves（および moves に紐づく統計）の整合性は merge worker が唯一の責任を持つ**。

- merge worker は `live.game.<gid>.moves.diff` を厳格に適用し、ギャップがあれば `request_snapshot` を発火して回復する。
- main thread は moves を「再マージしない」。
  - move を含む更新は worker がマージ済み snapshot を渡す（UI はそれをそのままキャッシュして描画）。
  - これにより main thread 側の `updateMoveCollections()` による “穴を作らないためのスキップ連鎖” を原理的に回避する。

---

## 4. 現時点で判明している根本原因クラス

ここでは「このクラスの問題があると、missing/illegalが起きる」という分類でまとめる。

### 4.1 moves（加算ログ）に対して “落としてOK” を混ぜている
- WSハブが topic単位で coalesce を行うと、`live.game.<gid>.diff` の中間moveが落ち得る。
- 中間moveが落ちた場合、クライアントは snapshot/replay で完全復旧できないと “穴” が永久化する。

### 4.2 旧時代の防衛的ロジック（Undo/修正を拒否）
過去の「順序保証が弱かった時代」の防衛コードが残ると、正当な巻き戻し/修正が拒否されて不整合を増幅する。

典型例:
- plyの減少を拒否（`Math.max(old,new)`系）
- 同一plyのmoves上書きを拒否（first-write-wins）

この種のコードは「正しい修正（待った/訂正）」を破壊的に無視する。

### 4.3 フロント合流（rAF）で move delta を落とす/潰す
- 受信頻度が高いと、同一rAFフレーム内で複数の move delta が届く。
- それらを「最後だけ適用」すると、中間が欠けて穴が生まれる（missing）または順序が崩れる（illegal）。

### 4.4 “defer/strip” した move を取り戻せない
- 「未来plyのmoveが来たので危ないから捨てる/遅延する」戦略自体は正しい場合があるが、
  捨てたあとに再適用できる仕組みがないと、穴が永久化する。

---

## 5. 現在行っている暫定対策（短期の安全化）

目的: “壊れたmoves列を作らない” を優先し、missing/illegalが出る条件を絞る。

- Undo/修正を拒否しない（巻き戻し、同一ply上書きを許可し、必要なら履歴truncate）
- rAF合流で move delta を落とさない（キューで順序通り適用）
- “未来plyのmove” を安易に圧縮して前に詰めない（indexずれ=永久破壊を避ける）
- stale snapshot による巻き戻りを抑止（ただし修復に必要なsnapshotまで捨てる危険もあるため要設計見直し）
- デバッグ用に tsshogi による合法手チェック（missing/illegalの検出）を導入

ただし、これらは **配信基盤がmovesを落とし得る**限り、根本解決にはならない。

---

## 6. 恒久設計案（メンテしやすく健全な形）

ここからが本題。「健全な設計」は、データの性質（加算ログ vs 状態）に合った配信・復旧を行うこと。

### 案A: moves と state を topic 分離（推奨）

#### 概要
- `live.game.<gid>.moves.diff`（加算ログ: 絶対欠損させない）
  - `move/currentPly` に加え、**着手に紐づく統計（eval/depth/seldepth/nodes/time_ms/wall_time_ms/latency...）も同じストリームに載せる**。
  - これらを state 側（analysis-only）に分離すると、フロントが「着手と紐づけられない値」として履歴コミットを拒否し、評価グラフが欠損する。
- `live.game.<gid>.analysis.diff`（最新表示用: 欠損許容）
  - 最新の評価値を表示するためのストリーム。履歴の確定値は `moves.diff` 側の `analysis_final` を使う。
- `live.game.<gid>.state.diff`（状態: coalesce/間引き可）
  - 例: clock / sfen（局面）/ メタ / 終局
- `live.game.<gid>.snapshot`（完全復旧用: moves+state を含む 最新基準の snapshot）

#### メリット
- “落として良い” と “落としてはいけない” が設計上・運用上明確になる。
- WSハブのdrop/coalesce戦略を topic別に安全に最適化できる。
- どの層で破綻したかを切り分けしやすい（movesに穴が出たらmoves経路の問題と確定できる）。

#### デメリット
- topic数が増える、購読管理が少し複雑になる。

#### 実装方針（例）
- サーバ:
  - move diff は `live.game.<gid>.moves.diff` に送る（payloadは ply + usi + 付随統計）
  - 最新評価は `live.game.<gid>.analysis.diff` に送る（欠損許容）
  - clock/局面/メタ/終局 は `live.game.<gid>.state.diff` に送る（coalesceしてOK）
  - snapshot_resolver は `*.snapshot` を返す（必要なら moves/state をまとめて返す）
- WSハブ:
  - `*.moves` は coalesce対象から除外
  - queue満杯時は `*.state` / `*.analysis` を優先的に落とす
- クライアント:
  - movesは「欠損不可」として厳格に適用（seqギャップは即snapshot要求）
  - analysis は最新優先（欠損/逆行は破棄でOK）
  - stateは後勝ち（last-write-wins）で良い

### 案A'（詳細仕様案）: Strict Moves + Latest Jump

**前提（要件）**
- **厳密な順序が必要**: 盤面（moves）。
- 評価グラフや統計（depth/nodes/time など）は **最新優先**（欠損許容・逆行は破棄）。
- **乱れたときは最新状態にジャンプ**（連続性よりも正しさを優先）。

この前提に合わせ、差分駆動を「厳密ストリーム」と「最新ストリーム」に分ける。

#### 6.A' 1. ストリーム定義
- `live.game.<gid>.moves.diff`  
  - **厳密順序**（gapがあれば即 snapshot）。
  - payload: `seq`, `ply`, `usi`, `sfen?`, `time_ms`, `nodes`, `depth`, `seldepth`, `eval`, `result_code?`, `pv?` など。
  - `__history_rev` は将来の巻き戻し対応用（現時点では **非運用**）。
- `live.game.<gid>.analysis.diff`  
  - **最新優先（欠損許容）**。順序厳密性は不要。
  - payload: `seq`, `ply`, `eval`, `nodes`, `depth`, `seldepth`, `time_ms`, `nps?` など。
  - *moves と同一 ply で整合する必要はあるが、gap/逆行は破棄でOK。*
  - `__history_rev` は将来の巻き戻し対応用（現時点では **非運用**）。
- `live.game.<gid>.state.diff`  
  - **最新優先**（coalesce可）。
  - payload: `clock`, `phase`, `meta` など。
- `live.game.<gid>.snapshot`  
  - 最新表示の基準となる snapshot。`moves + state`（analysis は任意）をまとめて返す。
  - `__history_rev` は将来の巻き戻し対応用（現時点では **非運用**）。

#### 6.A' 2. 受信・適用ルール（フロント）
- **共通ルール**
  - `seq` が飛んだら **即 snapshot 取得**（差分は破棄）。
  - `__history_rev` は現時点で未使用（将来の巻き戻し対応で導入予定）。
- **moves**
  - 連続 seq のみ適用。
  - `ply` の逆行や欠落を検知したら snapshot。
- **analysis**
  - 最新優先。欠損/逆行は **破棄**で良い（moves の整合性には影響しない）。
  - `ply` が `last_ply` または `last_ply + 1` 以外なら破棄。
- **state**
  - last-write-wins で上書き。
  - clock/phase は最新で良い（順序保証不要）。
- **snapshot**
  - 受信した瞬間に moves/state（analysis があれば含める）を全面置換。
  - 直後の diff 適用は `seq > snapshot.seq` のみ許可。

#### 6.A' 3. サーバ側の保証（WS/配信）
- `*.moves.diff` は **coalesce禁止**。
- `*.analysis.diff` / `*.state.diff` は **coalesce可**（最新優先）。
- queue 満杯時は **state/analysis を落としてもよい**が moves は落とさない。
  - 落とせない場合は **クライアントを切断**し、再接続で snapshot 復帰させる。
- snapshot_resolver は **最新**の moves/state を返す（analysis は欠損許容）。

---

## 7. 2026-01-03: 復帰/長期放置時の乖離調査と対処

### 7.1 観測された症状
- assignment 更新直後に `live.game.<gid>.snapshot` を要求するが、
  **moves_len=0 の空スナップショット**が頻発。
- その直後に moves.diff が高い `ply` で届き、クライアント側で
  **out-of-order として差分適用を拒否** → 追いつきが遅延。
- `ws-snapshot-stale` が多発し、**古い gid への snapshot 要求**が残る。

### 7.2 原因の推定
- assignment が短時間に何度も切り替わり、
  **「最新ではない gid への snapshot 要求」が発生**していた。
- サーバ側で assignment を publish するタイミングが
  **snapshot 更新より先**だったため、空スナップショットが返る競合があった。
- merge worker が assignment 更新ごとに即座に snapshot を要求するため、
  **短時間の churn で snapshot が追いつけない**状況が生まれていた。

### 7.3 適用した対処
- サーバ側で **snapshot 更新後に assignment を publish**するよう順序変更。
- `request_snapshot` が assignment 未確定でも **キャッシュがあれば返す**ように緩和。
- merge worker が **assignment ごとの snapshot 要求をデバウンス**。
  - 短時間に gid が変わる場合、**最終 gid のみを要求**。
- 空スナップショット後に高 ply move が来た場合、
  **moves.diff を fromSeq=0 で再要求**して追いつきを優先。

### 7.4 デバッグログの拡充
- サーバ: `ws-request_snapshot` に **client_id/worker_filter/reason** を追加。
- クライアント: `LiveWS` に **request_snapshot の reason と assignment 変更ログ**を追加。
  - assignment_rev の巻き戻りは無視するガードを導入。

### 7.5 期待する効果
- assignment churn による **古い gid への snapshot 要求の削減**。
- 空スナップショット起因の **out-of-order move** を緩和。
- 復帰直後や長期放置後でも **最新局面へのジャンプ**を優先。

#### 6.A' 4. 整合性の基準
- **最新基準 = snapshot**
- diff は「最新に近づけるためのヒント」だが、壊れていたら即破棄して snapshot に飛ぶ。

#### 6.A' 5. 期待される性質
- 長時間稼働でも `O(Δ)` 更新。
- 乱れた瞬間に最新へジャンプするため「壊れ続ける」状態を防ぐ。

### 案B: moves を “サーバ主導の連番 + ack/resend” にする

#### 概要
- moveイベントに `moveSeq`（0,1,2…）を付ける（topic seqとは別）
- クライアントは `ack(moveSeq)` を返し、欠けたら `resend(fromMoveSeq)` を要求
- snapshotは最終手段

#### メリット
- 1手単位で欠損復旧できる（snapshot依存が減る）。
- 「どの手が欠けたか」が明確で、デバッグが圧倒的に楽。

#### デメリット
- プロトコルが少し重くなる（ack/resendの実装と状態管理が増える）。

### 案C: snapshot を唯一の正として高頻度配信（最も単純だが重い）

#### 概要
- moves/stateを含む完全snapshotを一定間隔で配信し、クライアントはそれをそのまま描画
- diffはヒント（もしくは廃止）

#### メリット
- クライアント実装が単純化する（基本は“置き換え”）。

#### デメリット
- 通信量・CPU負荷が大きい（特にmoves配列が長い対局）。
- 高頻度配信は逆に負荷を増やしてドロップを誘発しやすい。

---

## 7. 推奨ロードマップ（安全に移行する順序）

### フェーズ0（即時の安全策）
- `ws_server.py` の coalesce 対象から `live.game.<gid>.moves.diff` を外し、moves は落とさない。
  - ただし queue満杯時に本当に送れない場合は別の対策が必要（後述）。

### フェーズ1（設計の恒久化）
- 案Aを実装（moves/stateのtopic分離）。
- 仕様は **案A'（Strict Moves + Latest Jump）** を正式化し、`moves/analysis/state/snapshot` の挙動を明文化。
- WSハブのdrop方針をtopic別に最適化:
  - `*.moves` は原則落とさない（必要ならクライアントを切断して再接続で復旧、等の戦略にする）
  - `*.state` / analysis / summary は落として良い

### フェーズ2（さらに堅牢化）
- 案B（moveSeq + ack/resend）を導入し、snapshot依存を減らす。

---

## 8. 追加で必要な観測（設計判断のため）

次の2点は設計を選ぶ上で重要。

1) WSハブの drop 統計
- `ws_server.py` は drop reason を内部でカウントしている。
- 「いつ」「どのtopic」が落ちているかを可視化すると、movesが落ちているのか、stateが落ちているのか確定できる。

2) snapshot_resolver の正しさ
- snapshotが「本当に最新のmoves列を持っているか」
- Undo/修正時に moves配列のtruncate/再構築が正しいか

---

## 9. 復帰時フローの正規仕様（唯一の経路）

復帰時の「最新 gid への追従」は **assignment を唯一の根拠**にして行う。
旧 gid へ引っ張られる問題を根絶するため、**復帰時フローを 1 本に固定**する。

### 9.1 ルール（必須）
- 復帰時は `live.assignment.snapshot` または `live.assignment.diff` を **最新のみ** 取得（fromSeqなし）。
- assignment 受信後、**merge worker が該当 gid の snapshot を要求**する。
- main thread から `moves.diff` を起点に復帰しない（古い gid を拾う原因になる）。
- move-gap の復帰要求は **merge worker 内のみ**で完結させる（main thread からは発火しない）。
- **assignment snapshot を受信するまで `live.game.*` は破棄する**（assignment が SoT であるため）。
- **`assignment_rev` が一致しない diff/snapshot は破棄する**（古い割当からの混入を遮断）。
- **未割当 gid の request_snapshot は禁止**（main thread / worker ともに送らない）。

### 9.2 データフロー（復帰時）
1) フロント → サーバ: `request_snapshot("live.assignment.diff", fromSeq=None)`
2) サーバ → フロント: assignment の最新 snapshot/diff
3) merge worker: assignment を反映し、`live.game.<gid>.snapshot` を要求
4) サーバ → フロント: 最新 snapshot を返送
5) merge worker: snapshot を適用して VM を更新（カード描画）

### 9.3 assignment の SoT（唯一の責務）
- assignment は **worker update 由来のみ**で決定する。
- `set_worker_snapshot` は assignment を更新しない（古い snapshot で上書きしない）。
- snapshot は「表示用の状態」であり、**割当の決定権を持たない**。

---

## 10. プロトコル定義（案A' 対応の仕様ドラフト）

この節は **案A'（Strict Moves + Latest Jump）** を前提とした具体的なペイロード仕様と適用ルールの定義。

### 10.1 共通フィールド

全ての diff/snapshot で以下を **共通** とする:

- `gid` (string): game id
- `seq` (number): topic単位の単調増加シーケンス
- `__history_rev` (number): ゲーム履歴の世代ID（巻き戻し/再試合/再構築で増加）。**現時点では未運用（実装上は未送出）**。
- `assignment_rev` (number): assignment 世代ID（割当変更のたびに増加）
- `server_ts` (number, ms): サーバ時刻（任意）
- `ply` は **相対手数**（開始局面からの手数）。初手は `1`。
  - 途中局面開始の場合は `initial_sfen` の手数で補正する（表示は `start_ply + ply`）。

**ルール**:
- `seq` は topic内で単調増加（欠損/逆行は異常）。
- `__history_rev` は現時点で未運用（将来的に導入する場合のみチェック）。

### 10.2 moves diff

**topic**: `live.game.<gid>.moves.diff`

**payload**
```json
{
  "gid": "g123",
  "seq": 456,
  "ply": 120,
  "usi": "7g7f",
  "sfen": "startpos ...",
  "analysis_final": {
    "eval": 23,
    "nodes": 123456,
    "depth": 18,
    "seldepth": 26,
    "time_ms": 150,
    "nps": 823040
  }
}
```

**ルール**
- `ply` は **strict** に連続（`last_ply + 1`）。破綻したら snapshot 要求。
- `analysis_final` は **任意**。bestmove 確定時の評価値があれば添付する。
- `sfen` は optional（ある場合は quick-resync に利用可能）。

### 10.3 analysis diff

**topic**: `live.game.<gid>.analysis.diff`

**payload**
```json
{
  "gid": "g123",
  "seq": 789,
  "ply": 120,
  "analysis": {
    "eval": 35,
    "nodes": 234567,
    "depth": 20,
    "seldepth": 28,
    "time_ms": 180
  }
}
```

**ルール**
- `analysis` は **現在検討中の手（ply）に対する最新評価**として扱う。
- `analysis.diff` は **落ちてもOK**（moves の整合性には影響しない）。
- `ply` が `last_ply` または `last_ply + 1` 以外なら **破棄**。
- analysis の新旧は `seq` で決定し、**古い seq は無視**。

### 10.4 state diff

**topic**: `live.game.<gid>.state.diff`

**payload**
```json
{
  "gid": "g123",
  "seq": 321,
  "clock": { "black_remain_ms": 12345, "white_remain_ms": 23456, "active": "black" },
  "meta": { "event": "..." }
}
```

**ルール**
- `state` は **last-write-wins** で上書き。
- `seq` の逆行は無視（ただし復旧には影響しない）。

### 10.5 snapshot

**topic**: `live.game.<gid>.snapshot`

**payload**
```json
{
  "gid": "g123",
  "seq": 999,
  "initial_sfen": "lnsgkgsnl/1r5b1/ppppppppp/9/9/9/PPPPPPPPP/1B5R1/LNSGKGSNL b - 1",
  "moves": [
    {"ply": 1, "usi": "7g7f", "analysis_final": {"eval": 10, "nodes": 1000, "depth": 8}},
    {"ply": 2, "usi": "3c3d", "analysis_final": {"eval": 12, "nodes": 2000, "depth": 9}}
  ],
  "result_code": 3,
  "state": {
    "clock": { "black_remain_ms": 12345, "white_remain_ms": 23456, "active": "black" }
  }
}
```

**ルール**
- snapshot は **最新表示の基準**。受信時点で moves/state を全面置換。
- snapshot 受信後は **topicごとの lastSeq を初期化**し、以後は `seq` が増加するもののみ適用。

### 10.6 エラー・リカバリの仕様

- **gap** を検知したら:
  1) `request_snapshot(live.game.<gid>.snapshot)`  
  2) 既存の diff 適用キューを破棄  
  3) snapshot受信後に再開

- snapshot が一定時間返らない場合:
  - UIは「最新が取得できない」状態として警告表示（silentリトライは禁止）。

---

### 10.7 assignment（復帰の唯一入口）

**topic**: `live.assignment.snapshot` / `live.assignment.diff`

**payload**
```json
{
  "assignments": { "0": "g123", "1": "g456" },
  "gids": ["g123", "g456"],
  "updatedAt": 1730000000000,
  "assignment_rev": 12
}
```

**ルール**
- 復帰時は **最新のみ取得**（fromSeqなし）。
- assignment を受け取ったら、merge worker が `live.game.<gid>.snapshot` を要求する。
- assignment は **worker update が唯一の真実**。snapshot 由来で更新しない。

---

## 11. フロント実装設計（案A' 対応の構成）

目的: **厳密順序ストリーム（moves）を merge worker で厳格に適用し、乱れたら即 snapshot にジャンプ**する。  
UIは「正規化済みの snapshot を読むだけ」に近づける。

### 11.1 データフロー（要約）

1) 復帰時は `live.assignment.*` を **最新のみ**取得  
2) merge worker が assignment を反映し、`live.game.<gid>.snapshot` を要求  
3) `ws.ts` で topic を判別し、`liveMergeWorker` に **moves/analysis/state/snapshot** を委譲  
4) merge worker が `seq` を検証し、差分適用 or snapshot要求  
5) merge worker が **正規化済み WorkerSnapshotRecord** を main thread に送る  
6) main thread は **差分合成せず** そのままカード更新へ渡す

### 11.2 担当責務の明確化

**merge worker の責務**
- `moves.diff` を厳密適用（seq/plyチェック）
- ギャップ検知時に `request_snapshot` を発火
- `analysis.diff` は最新優先（欠損/逆行は破棄）
- snapshot 受信時は **全面置換**、以後の diff は `seq > snapshot.seq` のみ許可
- `state.diff` は last-write-wins で上書き

**main thread の責務**
- worker が出した **整合済み snapshot** を描画するだけ
- moves の再マージは行わない（穴の作成を防ぐ）
- move-gap 由来の復帰要求は **行わない**（merge worker に集約）

### 11.3 ws.ts 側の変更点

1) **topic ルーティングの明確化**
   - `live.game.<gid>.moves.diff` → worker: `moves_diff`
   - `live.game.<gid>.analysis.diff` → worker: `analysis_diff`
   - `live.game.<gid>.state.diff` → worker: `state_diff`
   - `live.game.<gid>.snapshot` → worker: `snapshot`

2) **schema 検証は worker に集約**
   - main thread では envelope の外形チェックのみ
   - payload の seq/ply/内容の整合は worker で実施

### 11.4 merge worker 側の設計

**新しい内部状態**
- `lastSeqByTopic: Map<string, number>`
- `lastPlyByGid: Map<string, number>`
- `pendingSnapshotByGid: Set<string>`
- `latestAnalysisByGid: Map<string, { ply: number, analysis: AnalysisEntry }>`

**適用ルール（疑似コード）**
```
on moves_diff:
  if seq gap: request_snapshot(gid), drop
  if ply != lastPly+1: request_snapshot(gid), drop
  apply move (append), lastPly++
  attach analysis_final if present

on analysis_diff:
  if ply outside [lastPly, lastPly+1]: drop
  latestAnalysisByGid[gid] = { ply, analysis }

on state_diff:
  apply state last-write-wins

on snapshot:
  replace moves/state entirely
  set lastPly, lastSeq
```

### 11.5 main thread の表示戦略

- カード描画は **worker snapshot をそのまま使う**
- moves の整合は worker が保証済みなので、UI側は正しい前提で描画できる
- 既存の `updateMoveCollections()` や “future move skip” ロジックは **削除対象**

### 11.6 互換・段階移行

1) **旧 topic からの移行期間**
   - 旧 `live.game.<gid>.diff` は **互換/観測用のみ**（新規発行しない）。
   - 受信した場合は worker 側で **legacy 経路**として best-effort 処理し、moves の整合性保証対象外とする。seq gap による snapshot 要求は行わず、state 互換の更新のみ反映する。
2) **feature flag**
   - `useStrictStreams` を設け、旧/新の双方で動作確認できるようにする

### 11.7 影響範囲

**主要変更ファイル（想定）**
- `src/shogiarena/web/dashboard/frontend/src/modules/live/services/updates/ws.ts`
- `src/shogiarena/web/dashboard/frontend/src/modules/live/services/updates/worker/liveMergeWorker.ts`
- `src/shogiarena/web/dashboard/frontend/src/modules/live/components/cards/eventsController.ts`
- `src/shogiarena/web/dashboard/frontend/src/modules/live/components/cards/cardDisplay.ts`

---

## 12. サーバ配信設計（案A' 対応）

目的: **moves は絶対に coalesce しない**・gap が起きたら snapshot で復旧する設計をサーバ側で保証する。

### 12.1 topic と優先度の定義

| topic | 性質 | coalesce | drop方針 |
|---|---|---|---|
| `live.game.<gid>.moves.diff` | 厳密順序 | 禁止 | **落とさない**。必要なら client を切断して再接続で復旧 |
| `live.game.<gid>.analysis.diff` | 最新優先 | 許可 | drop可（最新表示のみを優先） |
| `live.game.<gid>.state.diff` | 最新優先 | 許可 | 低優先でdrop可 |
| `live.game.<gid>.snapshot` | 最新基準 | 許可 | 返せない場合は明示的に error |
| `live.summary.*` | 最新優先 | 許可 | 低優先でdrop可 |
| `live.assignment.*` | 最新優先 | 許可 | **復帰時は最新のみ返す**（fromSeqなし） |

### 12.2 ws_server のキュー管理方針

**ルール**
- queue が満杯なら **state/analysis/summary を優先的に落とす**
- moves の送信が詰まったら **client を切断**して再接続により復旧させる

**必要な変更（想定）**
- `_is_strict_moves_topic` の対象を `moves.diff` のみに限定
- coalesce 対象の topic から `moves.diff` のみを除外
- `queue_full` 時の優先度:  
  `moves` → disconnect  
  `analysis/state/summary` → drop/coalesce

### 12.3 snapshot_resolver の責務

- `live.game.<gid>.snapshot` は **常に最新**の moves/state を返す（analysis は欠損許容）
- snapshot 作成時の `__history_rev` は将来の巻き戻し対応で導入予定（現時点では未付与）
- 返却 payload は **フロントがそのまま replace できる**形に統一する

### 12.4 seq の扱い

- topicごとに `seq` を単調増加
- snapshot には `seq` を含める（その topic の最新 seq）
- diff の `seq` が snapshot を下回る場合はクライアント側で破棄する

### 12.5 後方互換 / 移行

移行期間中は旧 `live.game.<gid>.diff` が混在する可能性があるため、サーバ側は:
- 新 topic を優先的に配信
- 旧 topic は **互換/観測用のみ** に縮退。必要がある場合も state 互換の最小ペイロードのみを送る（moves を含めない）。

### 12.6 監視・メトリクス

最低限、次をサーバ側で可視化する:
- `moves` の **queue_full disconnect** 回数
- `analysis/state/summary` の drop 数
- snapshot_resolver の latency / error

---

## 13. 2026-01-04: 復帰時のstale assignment問題と遅延蓄積問題の修正

### 13.1 復帰時のstale assignment snapshot要求問題

#### 観測された症状
- タブを非表示にして復帰した際、`[ws-snapshot-stale]`ログが多発
- 古いgid（例: `v000023-*`）へのsnapshot要求が発生し、現在の割当（例: `v000025-*`）に追従できない
- assignment diffが復帰直後に処理され、古いgidへのsnapshot要求がキューされる

#### 原因
1. **復帰時に古いassignment diffが処理される**
   - visibility resume/ws_open時に、バッファされた古いassignment diffが処理される
   - これにより古いgidへのsnapshot要求が発火
2. **`scheduleAssignmentSnapshot`がstaleチェックをしていなかった**
   - デバウンス後に実行する時点でassignmentが変わっている可能性を考慮していなかった

#### 適用した対処

**1) `awaitingAssignmentSnapshot`フラグの導入**

`ws.ts`と`liveMergeWorker.ts`の両方に導入:

```typescript
// ws.ts (line 284, 607-608, 650-660, 1117)
let awaitingAssignmentSnapshot = false;

// visibility resume / ws_open 時
awaitingAssignmentSnapshot = true;

// assignment diff受信時
if (awaitingAssignmentSnapshot && !isAssignmentSnapshot) {
    console.info('[LiveWS] skipping assignment diff (awaiting snapshot after resume)');
    return;
}
if (isAssignmentSnapshot) {
    awaitingAssignmentSnapshot = false;
}
```

**2) `scheduleAssignmentSnapshot`でのstaleチェック**

```typescript
// liveMergeWorker.ts - scheduleAssignmentSnapshot内
const currentAssignment = assignmentsByWorker.get(workerIdx);
if (currentAssignment !== latestGid) {
    console.info('[LiveWorker] skipping stale assignment snapshot request', {
        workerIdx,
        pendingGid: latestGid,
        currentGid: currentAssignment ?? null,
    });
    pendingAssignmentSnapshotGid.delete(workerIdx);
    return;
}
```

#### 関連ファイル
- `src/shogiarena/web/dashboard/frontend/src/modules/live/services/updates/ws.ts`
- `src/shogiarena/web/dashboard/frontend/src/modules/live/services/updates/worker/liveMergeWorker.ts`

---

### 13.2 rAF遅延の蓄積問題（診断メトリクスのスロットリング）

#### 観測された症状
- ダッシュボードを長時間開いていると、`rafDelayMs`が徐々に増加
  - 初期: 50-60ms → 数十局後: 100-130ms
- `vmDelayMs`も同様に増加（6ms → 27ms）
- 対局数が増えるほど遅延が悪化

#### 原因の特定プロセス

**1) 追加した診断ログ**

```typescript
// eventsController.ts - rAF delay log
if (rafDelayMs > 50) {
    console.warn('[LiveEvents] rAF delay', {
        rafDelayMs,
        pendingWorkerRefresh: pendingWorkerRefresh.size,
        pendingVm: pendingVmByWorker.size,
        inFlight: inFlightUpdateByCardId.size,
        refreshAfterInFlight: refreshAfterInFlightByCardId.size,
        staleInFlight: staleCount,
        maxStaleMs,
    });
}

// flush timing log
if (pendingMs > 10 || vmMs > 10) {
    console.warn('[LiveEvents] flush timing', {
        pendingMs, vmMs, totalMs,
    });
}
```

**2) 診断結果**
- `inFlight: 0` - カード更新Promiseは溜まっていない
- `staleInFlight: 0` - ハングしているPromiseもない
- `pendingWorkerRefresh`と`pendingVm`は常に0-1 - バックログは溜まっていない
- **`vmMs`が増加傾向** - `flushPendingVm()`処理が重くなっている

**3) 根本原因の特定**

`recordLiveDiagnosticsMetric()`が毎回呼び出すたびに`refreshLiveNamespaceDiagnostics()`を実行していた。

この関数は以下の重い処理を行う:
- `meta.events.map()`でtimelineを毎回作成
- `Object.entries(meta.metrics).map()`で全メトリクスを処理
- 各メトリクスに対して`summarizeMetricWindow()`と`summarizeExtraWindow()`
- `downsampleValues()`で配列処理
- 大量の`Object.freeze()`呼び出し
- `CustomEvent`のディスパッチ

対局が進むにつれてメトリクスのサンプル数が増加し、この処理が重くなっていた。

#### 適用した対処

**`refreshLiveNamespaceDiagnostics`のスロットリング（1秒に1回）**

```typescript
// metrics.ts
const DIAGNOSTICS_REFRESH_THROTTLE_MS = 1000;
let lastDiagnosticsRefreshAt = 0;
let pendingDiagnosticsRefresh: ReturnType<typeof setTimeout> | null = null;

// recordLiveDiagnosticsMetric()の末尾
const now = performance.now();
if (now - lastDiagnosticsRefreshAt >= DIAGNOSTICS_REFRESH_THROTTLE_MS) {
    lastDiagnosticsRefreshAt = now;
    refreshLiveNamespaceDiagnostics(owner, meta);
} else if (!pendingDiagnosticsRefresh) {
    pendingDiagnosticsRefresh = setTimeout(() => {
        pendingDiagnosticsRefresh = null;
        lastDiagnosticsRefreshAt = performance.now();
        refreshLiveNamespaceDiagnostics(owner, meta);
    }, DIAGNOSTICS_REFRESH_THROTTLE_MS);
}
```

#### 修正後の効果
- rAF遅延が完全に解消（50ms以下）
- `live.cards.ply_lag`の`vm_delay_ms`: 平均 **4.4ms**（安定）
- `live.worker.vm`の`latency_ms`: 平均 **0.14ms**（安定）
- 長時間稼働でも遅延が蓄積しない

#### 関連ファイル
- `src/shogiarena/web/dashboard/frontend/src/modules/live/utils/liveNamespace/metrics.ts`
- `src/shogiarena/web/dashboard/frontend/src/modules/live/components/cards/eventsController.ts`

---

### 13.3 追加された診断機能

#### ブラウザコンソールからの診断

```javascript
// Worker側の診断情報を取得
window.__liveDiagnostics()

// 出力例（Worker側）:
// [LiveWorker] diagnostics {
//   snapshotsByGid: 1,
//   assignmentsByWorker: 1,
//   workersByGid: 1,
//   seqByTopic: 4,
//   pendingSnapshotTopics: 0,
//   ...
// }
```

#### rAF遅延ログの読み方

```
[LiveEvents] rAF delay {
  rafDelayMs: 97,           // rAFの発火遅延（50ms超で警告）
  pendingWorkerRefresh: 1,  // 待機中のworkerリフレッシュ数
  pendingVm: 1,             // 待機中のVM数
  inFlight: 0,              // 実行中のカード更新Promise数
  refreshAfterInFlight: 0,  // inFlight完了後に再スケジュールされる数
  staleInFlight: 0,         // 5秒以上残っているin-flight数
  maxStaleMs: 0             // 最も古いstale in-flightの経過時間
}
```

| 値が高い場合 | 原因 |
|---|---|
| `inFlight` | カード更新Promiseが溜まっている |
| `staleInFlight` | Promiseが解決されずハングしている |
| `pendingWorkerRefresh` | workerリフレッシュがバックログ |
| `pendingVm` | VM適用がバックログ |
| `rafDelayMs`のみ高い | 他のJS処理がメインスレッドを占有 |

---

### 13.4 学んだ教訓

1. **診断・メトリクス収集自体がパフォーマンスに影響する**
   - 高頻度で呼ばれる箇所での重い処理は必ずスロットリングする
   - `Object.freeze()`、`Object.fromEntries()`、`map()`の連続呼び出しは高コスト

2. **復帰時のフローは厳密に制御する**
   - バッファされた古いメッセージを処理しないガードが必要
   - assignment snapshotを受信するまで他のdiffは破棄すべき

3. **デバウンス後の実行時にも状態チェックが必要**
   - デバウンス期間中に状態が変わっている可能性を常に考慮する

### 13.5 復帰後の盤面破壊問題（stale game snapshot）

#### 問題
- タブ復帰後、最初の1〜2ゲームで盤面とmovesに乖離が発生
- `[LiveLegal]` エラーが出ないにもかかわらず盤面が「壊れて」見える
- コンソールには `out-of-order move detected` と `contiguousLen: 0` のログ

#### 原因分析
タブ非表示中に複数のゲーム切り替え（assignment変更）が発生した場合のタイミング問題：

1. **非表示中に連続してassignment diffが到着**
   - `v000025-pvsitrbv` → `v000025-xgazuoyi` → `v000026-xzpu36zd` → `v000026-srbdhjhs`
   - 各変更ごとにスナップショットリクエストが発行される

2. **`visibility_resume` で `resetState` が実行される**
   - `awaitingAssignmentSnapshot = true` に設定される
   - しかし、古いgidのスナップショットリクエストは既にサーバーに送信済み

3. **古いgidのスナップショット応答が到着**
   - `awaitingAssignmentSnapshot` フラグは **assignment stream** のみをブロック
   - **game snapshot** は別のチェックパスを通過してしまう
   - 結果として、古いゲームのスナップショットが新しいゲームに適用される

4. **`[LiveLegal]` エラーが出ない理由**
   - 合法手チェッカーはgidごとにリセットされる
   - 盤面の破壊は不正な手ではなく、**異なるゲームのスナップショット混入**が原因
   - moves配列自体は正しいが、表示している盤面が別のゲームのもの

#### 修正 (`liveMergeWorker.ts`)

```typescript
// handleGameSnapshot() の冒頭に追加
function handleGameSnapshot(topic: string, gid: string, payload: unknown): void {
    // After reset (e.g., visibility resume), ignore game snapshots until a fresh assignment snapshot
    // arrives. This prevents stale snapshot responses (requested before reset) from corrupting the view.
    if (awaitingAssignmentSnapshot) {
        console.info('[LiveWorker] skipping game snapshot (awaiting assignment snapshot after reset)', {
            topic,
            gid,
        });
        clearPending(topic);
        return;
    }
    // ... rest of the function
}

// handleGameDiff() の冒頭にも同様に追加
function handleGameDiff(topic: string, gid: string, payload: unknown): void {
    // After reset (e.g., visibility resume), ignore game diffs until a fresh assignment snapshot
    // arrives. This prevents stale diff responses from corrupting the view.
    if (awaitingAssignmentSnapshot) {
        clearPending(topic);
        return;
    }
    // ... rest of the function
}
```

#### 修正の効果
- `visibility_resume` → `resetState` 後、**assignment snapshot** が到着するまで
  **すべてのgame snapshot/diffを無視**する
- これにより、リセット前に送信されたスナップショットリクエストの応答が
  新しいゲームの表示を破壊することを防ぐ

#### 関連ファイル
- `liveMergeWorker.ts:handleGameSnapshot()` - game snapshotの処理
- `liveMergeWorker.ts:handleGameDiff()` - game diffの処理
- `liveMergeWorker.ts:handleAssignmentSnapshot()` - assignment snapshot処理時に `awaitingAssignmentSnapshot = false`

---

### 13.6 復帰時の盤面表示不整合問題（defer終了後のmoves再適用）

#### 問題
- タブから離れて復帰したとき、盤面と棋譜の対応がズレる
- 前回離れた局面を開始局面にしてmovesを再生しようとしている

#### 原因分析

復帰時のフローに問題があった：

1. **復帰時に`_deferBoardMovesUntil`が設定される（約350ms）**
   - `eventsController.ts`の`onVisibilityChange`で全worker-latestカードに設定

2. **defer期間中の動作**
   - `boardSync.ts`の`shouldDeferMoves`ブランチで`data.sfen`（現在局面）をボードにセット
   - `return`で抜けるため、`cardState.initial_sfen`は**更新されない**

3. **defer終了後の問題**
   - `cardState.initial_sfen`が前の値のままなので、`cardState.initial_sfen !== initSfen`チェックがfalseになり得る
   - 初期局面への再セット(`setPositionFromSFEN(initSfen)`)がスキップされる
   - 結果として「現在局面を開始局面にしてmovesを再適用」する不正な状態になる

#### 修正 (`boardSync.ts`)

**1) `deferJustEnded`の検出と強制再構築**

```typescript
const deferJustEnded = typeof deferUntil === 'number' && now() >= deferUntil;
const pendingRebuild = (cardState as { _pendingInitialRebuild?: boolean })._pendingInitialRebuild;
if (deferJustEnded) {
    delete (cardState as { _deferBoardMovesUntil?: number })._deferBoardMovesUntil;
    // moves未準備の場合はフラグを立ててdata.sfen表示を維持
    if (!Array.isArray(data.moves) || data.moves.length === 0) {
        (cardState as { _pendingInitialRebuild?: boolean })._pendingInitialRebuild = true;
        // data.sfen表示を維持
        if (source.startsWith('worker-latest:') && typeof data.sfen === 'string' && data.sfen.trim()) {
            adapter.setPositionFromSFEN(normalizeSFEN(data.sfen));
        }
        syncWorkerViewFromCard(cardState);
        return;
    }
}
```

**2) `_pendingInitialRebuild`フラグによる遅延再構築**

```typescript
// movesが揃った次回syncで強制再構築
const shouldForceRebuild = pendingRebuild && Array.isArray(data.moves) && data.moves.length > 0;
if (deferJustEnded || shouldForceRebuild) {
    if (pendingRebuild) {
        delete (cardState as { _pendingInitialRebuild?: boolean })._pendingInitialRebuild;
    }
    adapter.setPositionFromSFEN(initSfen);  // 真の初期局面
    cardState.initial_sfen = initSfen;
    (cardState as { _boardMovesKey?: string })._boardMovesKey = undefined;  // movesキャッシュクリア
    cache._boardGotoPly = 0;
}
```

#### 設計のポイント

1. **defer終了時にmoves未準備の場合**
   - 初期局面へのジャンプを避けて`data.sfen`表示を維持
   - `_pendingInitialRebuild = true`を設定して次回syncでの再構築を予約

2. **次回syncでmovesが揃った時**
   - `shouldForceRebuild`条件でフラグをチェック
   - フラグを削除し、`initSfen`で初期局面をセット
   - `_boardMovesKey`をクリアしてmovesの再適用を強制

3. **フラグのクリアは無条件**
   - `_pendingInitialRebuild`は`worker-latest:`のみで設定されるが、クリアは無条件
   - sourceが変更された場合やフラグが誤設定された場合の残留を防止

#### フロー図

```
タブ復帰 → defer期間(350ms) → defer終了
                               ↓
                    moves未準備？
                    ├─ Yes → _pendingInitialRebuild=true, data.sfen表示, return
                    │         ↓ (次回sync, movesが揃う)
                    │         shouldForceRebuild=true → 初期局面から再構築
                    └─ No  → 初期局面から再構築
```

#### 関連ファイル
- `boardSync.ts:92-138` - defer終了検出と再構築処理
- `eventsController.ts:769-774` - `_deferBoardMovesUntil`の設定

---

## 14. 実装との差分メモ（2026-01-04時点）

この節は「設計と実装のズレ」を明示しておくためのメモ。

- `__history_rev` は **未送出/未チェック**（設計上は将来導入）。
- `analysis.diff` は **最新優先・欠損許容**（coalesce可、gapで snapshot は要求しない）。
- snapshot は **最新表示の基準**であり、サーバキャッシュ遅延の可能性があるため、クライアントは **古いprefix snapshot を無視**する。
- 旧 `live.game.<gid>.diff` は **互換/観測用のみ**（新規発行しない想定）。
- `assignment_rev` は diff/snapshot に付与され、古い割当の混入を遮断する。
- WSハブは **moves のみ非coalesce**、バックプレッシャ時は非movesを捨てる/最悪切断で復旧する方針。

---

## 用語（この資料内の定義）

- contiguousLen: moves配列の先頭から空文字/欠損が出るまでの連続長
- hole（穴）: contiguousLen < movesLen で、かつ後ろに非空要素がある状態
- 最新基準 snapshot: 欠損復旧・整合性修正の基準として採用する snapshot
- coalesce: 同一topicのメッセージを最新だけ残し、それ以前を捨てる圧縮
- rAF delay: `requestAnimationFrame`がスケジュールされてから実際に発火するまでの遅延
- stale assignment: 現在の割当と異なる古いgidへの参照
