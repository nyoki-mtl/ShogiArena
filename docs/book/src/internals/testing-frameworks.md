# テストフレームワーク概説

> **前提知識**：[統計的検定とチューニング](./index.md) の全体像を把握していること

OpenBench、Fishtest、Cutechess は、チェスエンジンの対局、統計的検定、チューニングを支えるツールです。
それぞれの機能と、ShogiArena の OpenBench サーバ連携や将棋への対応を紹介します。

## OpenBench

### 分散テストと統計的検定の統合

[OpenBench](https://github.com/AndyGrant/OpenBench) は、Andrew Grant が開発したオープンソースのエンジンテストフレームワークです。
分散テストと統計的検定を一つの Web プラットフォームで扱います。
複数のマシン（ワーカー）でテストを並列実行し、中央サーバで結果を集約して判定します。

### アーキテクチャ

```text
┌──────────────────────────────────────────┐
│            OpenBench サーバ               │
│        （Django Web アプリケーション）       │
│                                          │
│  ┌─────────┐  ┌─────────┐  ┌─────────┐  │
│  │  Test   │  │ Result  │  │ Machine │  │
│  │ モデル  │  │ モデル  │  │ モデル  │  │
│  └─────────┘  └─────────┘  └─────────┘  │
│                    ▲                     │
└────────────────────┼─────────────────────┘
                     │ REST API
        ┌────────────┼────────────┐
        │            │            │
   ┌────▼────┐  ┌────▼────┐  ┌────▼────┐
   │Worker A │  │Worker B │  │Worker C │
   │(対局実行)│  │(対局実行)│  │(対局実行)│
   └─────────┘  └─────────┘  └─────────┘
```

- **サーバ**：Django ベースの Web アプリケーション。テストの作成、管理、結果集約を担う
- **ワーカー**：各マシンで対局を実行し、結果を定期的にサーバに報告する
- **REST API**：ワーカーの登録、ワークロード取得、結果提出、ハートビートを処理

### サポートするテストの種類

OpenBench は以下の 4 種類のテストをサポートしています。

| テストタイプ | 目的 | 統計手法 |
|:---|:---|:---|
| **SPRT** | パッチの強さを検証 | 逐次確率比検定 |
| **SPSA** | パラメータの自動チューニング | 同時摂動確率近似法 |
| **GAMES** | 固定局数での評価 | Elo 推定 + 信頼区間 |
| **DATAGEN** | 学習データの生成 | なし |

集計と実行の面では、次の機能を備えています。

- **五項分布スコアリング**：ペアゲームの結果を 5 カテゴリで追跡し、高精度な分析を提供
- **三項分布スコアリング**：従来の勝ち、引き分け、負けの 3 カテゴリも並行して追跡
- **ワーカー管理**：CPU コア数やスレッド数に基づくワークロード割り当て

### ShogiArena との連携

ShogiArena は `OpenBenchClient` クラスで OpenBench 互換の API クライアントを実装しています。
これにより、ShogiArena で実行した対局結果を OpenBench サーバ（または ShogiBench サーバ）に直接提出できます。

主な連携機能は次のとおりです。

- **既存テストへの参加**：サーバ上のテストにワーカーとして参加し、結果を提出
- **テスト作成**：ShogiArena から新規テストを作成し、他のワーカーと分散テストを実施
- **ペアリング検出**：ゲーム名のパターン（`g{round}-...`）から先後ペアを検出し、五項分布カウンタを更新
- **ハートビート**：定期的な接続確認で、サーバとのセッションを維持

```text
設定例（arena 設定 YAML）:

openbench:
  enabled: true
  server: "https://example.com"
  mode: "existing_test"       # 既存テストに参加
  target_test_id: 12345
  submit_interval_games: 100  # 100 局ごとに結果を提出
```

## Fishtest

### ボランティア分散によるテストインフラ

[Fishtest](https://tests.stockfishchess.org/) は、チェスエンジン「Stockfish」の開発チームが運営する公式テストインフラです。
世界中のボランティアが提供する数百台のマシンで、提出されたパッチを 24 時間体制でテストしています。

Fishtest では、STC と LTC の検定を開発手順に組み込んでいます。
「パッチを投稿 → STC で高速検証 → LTC で最終確認 → マージ」というフローによって、
Stockfish は統計的に裏付けられた改善を積み重ねています。

### テスト結果の読み方

Fishtest の Web インターフェースでは、各テストの結果が以下のような形式で表示されます。

```text
SPRT: elo0: -1.75 alpha: 0.05 elo1: 0.25 beta: 0.05 (normalized)
Pentanomial: [55, 11390, 30659, 11490, 70]
LLR: 2.94 [-2.94, 2.94] (accepted)
```

| 項目 | 意味 |
|:---|:---|
| **SPRT** | テストパラメータ。elo0/elo1 は仮説の Elo 差、alpha/beta は誤り率 |
| **(normalized)** | nElo（正規化 Elo）で閾値を設定していることを示す |
| **Pentanomial** | 五項分布のカウント \\([n_{0.0}, n_{0.5}, n_{1.0}, n_{1.5}, n_{2.0}]\\) |
| **LLR** | 現在の LLR 値と判定範囲 \\([\text{lower}, \text{upper}]\\) |
| **(accepted)** | H1 を採択（改善あり）。**(rejected)** なら H0 を採択（改善なし） |

五項分布の合計 × 2 が総対局数です。
上の例では (55+11390+30659+11490+70)×2 = 107,328 局でテストが完了しています。

elo0/elo1 の設定は、変更の種類によって使い分けます。

- コードの削除や簡略化：`[-1.75, 0.25]`（劣化がないことの確認）
- アルゴリズム改善：`[0, 2]` や `[0.5, 2.5]`（改善効果の実証）

> **参考**：テスト結果の背景にある統計理論の詳細は [SPRT](./sprt/index.md) と [五項分布モデル](./sprt/pentanomial.md) を参照してください。

### SPRT Calculator

Fishtest は、必要な対局数を事前に見積もるための [SPRT Calculator](https://tests.stockfishchess.org/sprt_calc) を提供しています。
elo0、elo1、引き分け率、実際の Elo 差を入力すると、テスト完了までの期待対局数を計算できます。
テストの計画段階で、そのパラメータ設定に何局必要かを把握できます。

### STC→LTC ワークフロー

Fishtest が確立した**階層テスト戦略**は、エンジン開発の標準的な手順として広く採用されています。

```text
パッチ投稿
    │
    ▼
┌────────────────────────┐
│  STC テスト（短時間制御） │
│  例: 10+0.1秒          │
│  SPRT: elo0=0, elo1=5  │
│  α=0.05, β=0.05        │
└───────────┬────────────┘
            │ Pass
            ▼
┌────────────────────────┐
│  LTC テスト（長時間制御） │
│  例: 60+0.6秒          │
│  SPRT: elo0=0, elo1=5  │
│  α=0.05, β=0.05        │
└───────────┬────────────┘
            │ Pass
            ▼
    master にマージ
```

この戦略には次の利点があります。

- **STC で高速フィルタリング**：弱いパッチを早期に棄却し、計算資源を節約
- **LTC で信頼性の確保**：STC を通過したパッチだけを長時間で再検証
- **二段階のゲートキーピング**：偽陽性のリスクを低減

### fishtest-spsa-lab

Fishtest コミュニティからは、SPSA アルゴリズムの最適化研究も生まれています。
fishtest-spsa-lab は、Fishtest で使用される SPSA の収束特性を改善するための実験プロジェクトで、
以下のアルゴリズムバリアントを比較検証しています。

| アルゴリズム | 特徴 |
|:---|:---|
| **Classic SPSA** | Fishtest の現行実装。固定ゲインスケジュール |
| **Schedule-Free SGD** | 学習率スケジュールが不要な最適化手法 |
| **Schedule-Free Adam** | パラメータごとに異なる感度に対応 |

ShogiArena の LTC 回帰テスト機能は、この研究から着想を得ています。

### ShogiArena への影響

| 知見 | ShogiArena での実装 |
|:---|:---|
| SPRT の設計 | [sprt_service.py](https://github.com/nyoki-mtl/ShogiArena/blob/main/src/shogiarena/_core/contexts/game_session/application/sprt_service.py) |
| 五項分布モデル | [pentanomial.py](https://github.com/nyoki-mtl/ShogiArena/blob/main/src/shogiarena/_core/shared/kernel/statistics/pentanomial.py) |
| STC→LTC ワークフロー | LTC 回帰テスト（`LtcRegressionConfig`） |
| 統計的方法論 | SPRT パラメータのデフォルト値設計 |

## Cutechess

### 参照実装としてのトーナメントマネージャ

[Cutechess](https://github.com/cutechess/cutechess) は、チェス用のトーナメントマネージャです。
コマンドライン版（cutechess-cli）と GUI 版の両方を提供し、
エンジン間の対局実行、トーナメント管理、棋譜出力をまとめて扱えます。

SPRT や裁定ロジックの参照実装としても使われています。

> **補足**：cutechess-cli の後継として、[fastchess](https://github.com/Disservin/fastchess) が開発されています。
> より効率的な並列処理と改善された SPRT 実装を提供しています。

### トーナメントフォーマット

| フォーマット | 説明 | 用途 |
|:---|:---|:---|
| **Round-robin** | 全エンジン同士の総当たり | 全体のランキング作成 |
| **Gauntlet** | 1 エンジン vs 他の全エンジン | 特定エンジンの相対評価 |
| **Knockout** | 勝ち抜きトーナメント | 最強エンジンの選出 |
| **Pyramid** | ピラミッド型の対戦 | 段階的な強さの比較 |

### ShogiArena への影響

| 影響を受けた領域 | ShogiArena での実装 |
|:---|:---|
| SPRT 実装の設計 | [sprt_service.py](https://github.com/nyoki-mtl/ShogiArena/blob/main/src/shogiarena/_core/contexts/game_session/application/sprt_service.py)（cutechess の `sprt.cpp` を参考） |
| 裁定ロジック | [adjudication.py](https://github.com/nyoki-mtl/ShogiArena/blob/main/src/shogiarena/_core/contexts/match/domain/adjudication.py) |
| 持ち時間管理 | [time_control.py](https://github.com/nyoki-mtl/ShogiArena/blob/main/src/shogiarena/_core/shared/kernel/time_control.py) |
| トーナメント形式 | Round-robin、スイス式をサポート |

## ShogiArena の位置付け

### 機能比較

| 特徴 | OpenBench | Fishtest | Cutechess | ShogiArena |
|:---|:---:|:---:|:---:|:---:|
| **SPRT** | ✓ | ✓ | ✓ | ✓ |
| **SPSA** | ✓ | ✓ | ✗ | ✓ |
| **五項分布** | ✓ | ✓ | ✓ | ✓ |
| **分散テスト** | ✓ | ✓ | ✗ | ✓（OpenBench 連携） |
| **トーナメント形式** | ✗ | ✗ | ✓ | ✓ |
| **Web ダッシュボード** | ✓ | ✓ | ✗ | ✓ |
| **LTC 回帰テスト** | △ | ✓ | ✗ | ✓ |
| **USI プロトコル** | ✗ | ✗ | ✗ | ✓ |
| **将棋ルール対応** | ✗ | ✗ | ✗ | ✓ |

### 将棋固有の適応

ShogiArena は、将棋のプロトコル、棋譜形式、ルール、持ち時間に対応しています。

プロトコルとフォーマット

- **USI プロトコル**：チェスの UCI に代わり、将棋標準の USI を完全実装
- **SFEN 記法**：チェスの FEN/EPD に代わり、SFEN を局面表現に使用
- **KIF/CSA 形式**：チェスの PGN に代わる棋譜フォーマットをサポート

将棋ルール

- **千日手**：同一局面が 4 回出現した場合の引き分け判定
- **持将棋**：入玉状態での点数計算による引き分けと勝敗の判定
- **入玉宣言勝ち**：入玉宣言法に基づく勝利判定
- **反則検出**：二歩、打ち歩詰め、行き所のない駒などの反則検出

持ち時間

- **秒読み（Byoyomi）**：フィッシャー方式に加えて、日本将棋特有の時間制御をサポート

### OpenBench API 互換性

ShogiArena の OpenBench クライアントは、以下の API エンドポイントと互換性があります。

| エンドポイント | 機能 |
|:---|:---|
| `clientGetWorkload` | ワークロード（テスト割り当て）の取得 |
| `clientSubmitResults` | 三項/五項分布の結果提出 |
| `clientHeartbeat` | ワーカーの接続確認 |
| `scripts`（`action=CREATE_TEST`） | 新規テストの作成 |

これらの API を通じて、既存の OpenBench サーバと分散テストを実行できます。

## 実装リファレンス

| ファイル | クラス/関数 | 役割 |
|---------|----------|------|
| [openbench](https://github.com/nyoki-mtl/ShogiArena/tree/main/src/shogiarena/_core/contexts/game_session/adapters/openbench) | `OpenBenchClient` ほか | OpenBench API 連携 |
| [sprt_service.py](https://github.com/nyoki-mtl/ShogiArena/blob/main/src/shogiarena/_core/contexts/game_session/application/sprt_service.py) | `Sprt` | SPRT 実装（cutechess ベース） |
| [adjudication.py](https://github.com/nyoki-mtl/ShogiArena/blob/main/src/shogiarena/_core/contexts/match/domain/adjudication.py) | `AdjudicationConfig` | ゲーム裁定ロジック |
| [time_control.py](https://github.com/nyoki-mtl/ShogiArena/blob/main/src/shogiarena/_core/shared/kernel/time_control.py) | `TimeControlLimits`, `GameClock` | 持ち時間管理 |
| [usi_engine_session.py](https://github.com/nyoki-mtl/ShogiArena/blob/main/src/shogiarena/_core/platform/engine_runtime/usi_engine_session.py) | `AsyncUsiEngine` | USI プロトコル実装 |
| [schedule_generation.py](https://github.com/nyoki-mtl/ShogiArena/blob/main/src/shogiarena/_core/contexts/tournament/application/schedule_generation.py) | `GameScheduler` ほか | 対局スケジューリング |

## 参考文献

- OpenBench: <https://github.com/AndyGrant/OpenBench>
- Fishtest: <https://tests.stockfishchess.org/>
- [Statistical Methods and Algorithms in Fishtest](https://official-stockfish.github.io/docs/fishtest-wiki/Fishtest-Mathematics.html) — Fishtest で使用されている統計手法の包括的な解説
- [SPRT Calculator](https://tests.stockfishchess.org/sprt_calc) — 必要な対局数の事前計算ツール
- fishtest-spsa-lab: <https://github.com/vondele/fishtest-spsa-lab>（SPSA 最適化研究）
- Cutechess: <https://github.com/cutechess/cutechess>
- fastchess: <https://github.com/Disservin/fastchess>（cutechess-cli の後継）
- [Chess Programming Wiki: SPRT](https://www.chessprogramming.org/Sequential_Probability_Ratio_Test)
- [Chess Programming Wiki: Match Statistics](https://www.chessprogramming.org/Match_Statistics#SPRT)

## 次に読む

→ **[用語集](./glossary.md)**：本ページで登場した用語のリファレンス

→ **[統計的検定とチューニング](./index.md)**：ShogiArena の統計モジュールの理論的背景
