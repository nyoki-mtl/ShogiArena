# Instances 設計方針

このドキュメントは、インスタンス（ローカル/リモート）の扱いに関する設計方針と運用上の前提をまとめます。
実装の挙動とダッシュボードの表示仕様は本書を正とします。

## 目的

- インスタンスの収容能力を安全に扱い、過負荷とリークを防ぐ
- ダッシュボードの更新を「SSE中心・RESTは補完」に統一する
- SSH/リモート運用の前提を明確化する

## 用語

- **slots**: 1台が同時に処理できる対局数。原則は「並列ゲーム数の上限」。
- **max_engines**: 1台で同時に起動できるエンジンプロセスの上限。
- **engine_processes**: 現在起動中のエンジン数（実測値）。
- **capacity known/unknown**: 収容能力が「明示されているかどうか」。

## 収容能力の扱い（slots / max_engines）

### slots の基本ルール

- `slots <= 0` は **auto** とみなす。
- auto の場合は OS から取得した `cpu_count` を上限とする。
- OS の値が取得できない場合は **capacity unknown** とする。
- capacity unknown の場合は「安全側」で扱い、予約・割り当ては抑制する。

### max_engines の基本ルール

- `max_engines <= 0` は **無制限** とみなす。
- 上限がある場合は、**engine_processes** と比較して新規生成をブロックする。
- `engine_processes` は実測であり、予約ではない。

### capacity unknown の意味

- capacity unknown のインスタンスには **新規ジョブを割り当てない**。
- UI では `?` 表記で「未確定」を明示する。
- リモートで `cpu_count` 取得に失敗した場合の保険として機能する。

## エンジン生成とエンジン数の制御

- 新規エンジン生成時に `engine_processes` を増やし、終了時に減らす。
- `engine_processes` が `max_engines` を超える場合は生成を拒否する。
- この制御は「実測ベース」であり、予約や推定は行わない。

## ダッシュボード更新方針（SSE + REST）

### 基本方針

- **初回のみスナップショット** を取得し、以降は SSE の差分で追従する。
- REST の定期ポーリングは行わない。
- SSE が欠落・不整合の場合のみ REST を補完として使う。

### SSE のイベント

- `instances` : 初期スナップショット
- `instances_delta` : 変更差分

### フォールバック条件（例）

- SSE 更新が一定時間届かない
- 差分適用に失敗した
- 必須フィールドが欠落している

## health_check の扱い

- health_check 完了時に **即時で SSE を push** する。
- health_check 連打による負荷を避けるため、**サーバ側で最小間隔**を設ける。
  - 環境変数 `SHOGI_ARENA_DASHBOARD_HEALTH_CHECK_MIN_INTERVAL` で調整する。

## SSH / リモートインスタンス運用

### 前提

- リモートは **bash 前提** で運用する。
- Windows でのリモート運用はサポート範囲外。
- ローカルは Windows 利用を許容する（パスや実行環境の差分に注意）。

### project_root の扱い

- `project_root` は **リモート側のパス** として解釈する。
- `$HOME` や `~` を含む場合は **リモートのホーム** を意味する。
- ローカル環境で `~` を展開してしまうような実装は避ける。

### SSH の安全性

- `StrictHostKeyChecking` は **デフォルトで有効**。
- 必要なら明示的に無効化するが、運用時は推奨しない。

## 設計上の注意点

- 例外の握りつぶしは禁止。原因を明示して失敗する。
- 予約/解放漏れは最優先で防ぐ（finally で必ず解放）。
- 収容能力が不明な場合は「割り当てない」方針を優先する。

## 関連ファイル

- 例: `configs/resources/instances/example.yaml`
- 実装: `src/shogiarena/arena/instances/`
- ダッシュボード: `src/shogiarena/web/dashboard/`
