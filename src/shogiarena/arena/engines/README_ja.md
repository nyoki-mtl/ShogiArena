# Engines モジュール概要

このディレクトリは、USI 対応エンジンをアリーナ内で動かすための基盤を提供します。モジュールごとに責務を明確に分割し、オーケストレータやランナーから組み合わせられる構造になっています。以下は主なモジュールとクラスの一覧です。

## モジュール索引

| モジュール | 主な型 | 役割 |
| --- | --- | --- |
| `engine_factory.py` | `EngineFactory` | `UsiEngineConfig` の読み込み、バイナリ解決（ローカル／リモート）、アーティファクト配備を行い、初期化済みの `AsyncUsiEngine` を返すファクトリ。 |
| `time_control.py` | `TimeControlLimits`, `TimeControl`, `TimeControlSpecParseError` | 対局者ごとの持ち時間状態を管理し、UI 向けの spec 文字列との相互変換を担う。ランナーはここから残り時間を取得し、思考リクエスト生成に利用する。 |
| `usi_bridge.py` | `AsyncUSIProcessBridgeProtocol` | USI ブリッジの構造的インターフェース。起動手段や通信手段を `AsyncUsiEngine` から分離し、エンジン関連モジュール内で責務を明確化する。 |
| `usi_bridge_spawner.py` | `SpawnerBackedUSIBridge` | ローカル／SSH のどちらにも対応したプロセス起動レイヤー。`AsyncUSIProcessBridgeProtocol` を実装し、共通の API で `AsyncUsiProcess` から操作できるようにする。 |
| `usi_config.py` | `UsiEngineConfig` | エンジン設定 YAML を厳密な構造体に正規化し、パス解決やオーバーライド適用を行う。 |
| `usi_engine.py` | `AsyncUsiEngine`, `AnalysisHandle`, `UsiMateResult` | 高レベルの USI セッション管理。ハンドシェイク、オプション設定、`info`/`bestmove` のストリーミング、思考・解析セッションの制御を担う。 |
| `sync_usi_engine.py` | `SyncUsiEngine`, `SyncAnalysisHandle` | `AsyncUsiEngine` をバックグラウンドループで実行し、同期コードから扱えるようにするラッパ。 |
| `usi_process.py` | `AsyncUsiProcess` | `AsyncUSIProcessBridgeProtocol` の状態機械。単一の start/stop を保証し、I/O の前提条件をチェックする。 |
| `usi_protocol.py` | `UsiProtocolParser`, `UsiOption`, `UsiIdField` | `id`/`option`/`info`/`bestmove` などのテキストを構造化データへ変換する純粋パーサ。 |
| `usi_think.py` | `UsiThinkRequest`, `request_from_time_controls`, `normalize_searchmoves` | `go` コマンドのパラメータを表現し、タイムコントロール情報から思考リクエストを生成する。 |
| `usi_types.py` | `UsiEvalValue`, `UsiBound`, `UsiThinkPV`, `UsiThinkResult` | USI 特有の値オブジェクト群。評価値、境界情報、`info` 行の結果、`bestmove` 結果などを表現する。 |

## 典型的なフロー

1. トーナメントオーケストレータがエンジン設定ファイルを読み込み、`EngineFactory` に `AsyncUsiEngine` の生成を依頼する。
2. `EngineFactory` は `SpawnerBackedUSIBridge` を用いてバイナリを起動し、`AsyncUsiProcess` でラップした `AsyncUsiEngine` を返す。
3. 対局中は各プレイヤーの `TimeControl` が残り時間を追跡し、手番時に `request_from_time_controls(...)` を呼び出して `UsiThinkRequest` を組み立てる。
4. `AsyncUsiEngine` は `UsiProtocolParser` で `info` 行を `UsiThinkPV` に変換し、`UsiEvalValue` や `UsiBound` を通じて評価値を保持する。
5. `bestmove` が返ると `UsiThinkResult` に最新 PV が紐づけられ、ランナーが盤面更新と時間消費処理を行う。

## 拡張ポイント

- **新しい起動手段を追加する場合**: `AsyncUSIProcessBridgeProtocol` を実装し、`AsyncUsiProcess` に渡す。`usi_bridge.py` と `SpawnerBackedUSIBridge` が参考実装。
- **時間制御ポリシーの拡張**: `TimeControlLimits` を拡張するか、`usi_think.py` に新しいビルダーを追加し、`UsiThinkRequest` の生成ロジックを差し替える。
- **独自トークンの解析**: エンジンが非標準の `info` や `option` を出す場合は、`usi_protocol.py` に専用パーサを追加する。

モジュールごとの責務を保ちながら拡張することで、オーケストレータ／ランナー／UI など上位レイヤーの影響を最小限に抑えられます。
