# LTC 回帰テスト

> **前提知識**: [SPSA](./index.md)、[SPRT](../sprt/index.md)

## このページの要点

- LTC（Long Time Control）回帰テストは、短時間チューニングで得たパラメータが**長時間対局でも有効か**を検証する
- 一定回数の SPSA 更新ごとに自動的に実行され、SPRT またはスコアベースの基準でパス/フェイルを判定する
- フェイルした場合、パラメータは前回のパス時点まで**自動リバート**される
- 「短時間では有効だが長時間では無効」な局所最適への陥落を防ぐ安全装置

## なぜ LTC 回帰テストが必要か

SPSA チューニングは通常、短い持ち時間（STC: Short Time Control）で実行されます。
これは対局のスループットを最大化するためです。

しかし、STC で最適なパラメータが LTC でも最適とは限りません。

```text
STC の特性:
- 読みの深さが浅い → 評価関数の粗い調整が有効
- 時間切れ負けのリスク → 高速な指し手生成が有利
- ノイズが大きい → 偶然の勝ちが多い

LTC の特性:
- 読みが深い → 精密な評価が重要
- 時間に余裕がある → 探索効率が重要
- ノイズが小さい → 真の実力差が現れやすい
```

LTC 回帰テストは、STC でのチューニング結果が LTC でも性能劣化していないことを定期的に確認します。

## 動作の流れ

```text
SPSA 更新ループ
  │
  ├─ 更新 1 ... 49
  │
  ├─ 更新 50 (every_n_updates=50)
  │     │
  │     └─► LTC 回帰テスト実行
  │           │
  │           ├─ パス → 現在のパラメータを基準点として保存
  │           │
  │           └─ フェイル → 前の基準点までリバート
  │
  ├─ 更新 51 ... 99
  │
  ├─ 更新 100
  │     └─► LTC 回帰テスト実行
  │           ...
  └─ ...
```

## 設定

```python
class LtcRegressionConfig(BaseModel):
    enabled: bool = False
    every_n_updates: int = 0        # 何回の更新ごとに実行
    total_pairs: int = 0            # 実行するゲームペア数
    time_control: TimeControlLimits | None = None  # LTC 用タイムコントロール
    pass_criteria: LtcPassCriteria | None = None   # パス判定基準
```

### パス判定基準

```python
class LtcPassCriteria(BaseModel):
    min_winrate: float | None = None    # 最低勝率（例: 0.55）
    max_elo_drop: float | None = None   # 最大 Elo 低下
    sprt: SprtConfig | None = None      # SPRT による判定
```

3 種類の基準を組み合わせ可能です。`max_elo_drop` は正値・負値のどちらでも同じ許容低下量として扱います。

### 設定例

```yaml
ltc_regression:
  enabled: true
  every_n_updates: 50        # 50 更新ごとに実行
  total_pairs: 200           # 最大 200 ペア
  time_control:
    type: "byoyomi"
    main_time: 60            # 本時間 60 秒
    byoyomi_time: 1          # 秒読み 1 秒
    byoyomi_periods: 30      # 秒読み 30 回
  pass_criteria:
    min_winrate: 0.50         # 勝率 50% 以上
    sprt:
      elo0: 0.0
      elo1: 5.0
      alpha: 0.05
      beta: 0.05
```

## 判定ロジック

### LTC 対局の実行

チューニング済みパラメータ vs ベースラインパラメータで対局します。

```python
async def run_ltc_regression(
    orchestrator, *,
    tuned_params: list[ParamEntry],
    baseline_params: list[ParamEntry],
    ...
) -> dict[str, Any]:
    if sprt_config is not None:
        sprt = Sprt(
            elo0=float(sprt_config.elo0),
            elo1=float(sprt_config.elo1),
            alpha=float(sprt_config.alpha),
            beta=float(sprt_config.beta),
        )

    for pair_idx in range(total_pairs):
        score, gi_b, gi_w = await orchestrator._run_game_pair(...)

        # SPRT に結果を提出
        _submit_to_sprt(gi_b, tuned_as_black=True)
        _submit_to_sprt(gi_w, tuned_as_black=False)

        # SPRT が判定に到達したら早期終了
        if sprt_decision != SprtDecision.CONTINUE:
            break
```

### パス/フェイルの判定

```python
status = "passed"

# 勝率チェック
if min_winrate is not None and winrate < min_winrate:
    status = "failed"

# SPRT チェック
if sprt_decision == SprtDecision.ACCEPT_H0:
    status = "failed"     # 改善なしと判定
elif sprt_decision == SprtDecision.CONTINUE:
    status = "pending"    # 判定未確定

```

### パス時の処理

```python
if status == "passed":
    # 現在のパラメータを新しい基準点として保存
    self._store_ltc_baseline(post_update_snapshot, update_idx)
```

### フェイル時の処理

```python
elif status == "failed":
    # 前の基準点までパラメータをリバート
    params.clear()
    params.extend(baseline_params)
    write_params(params_path, params)
```

## 判定基準の選び方

### SPRT ベース（推奨）

統計的に厳密な判定が可能です。SPRT は対局数を動的に調整するため、効率的です。

```yaml
pass_criteria:
  sprt:
    elo0: -5.0    # 5 Elo の劣化まで許容
    elo1: 0.0     # 改善なしが帰無仮説
    alpha: 0.05
    beta: 0.05
```

> **注**: LTC 回帰では「劣化していないこと」を確認するため、elo0 と elo1 の設定が通常の SPRT と逆になることがあります。

### 勝率ベース

シンプルですが、サンプルサイズの考慮が不十分な場合があります。

```yaml
pass_criteria:
  min_winrate: 0.50   # 最低でも 50% 以上
```

### 複合基準

複数の基準を組み合わせることで、より堅牢な判定が可能です。  

```yaml
pass_criteria:
  min_winrate: 0.48        # 最低勝率
  max_elo_drop: 10.0       # 最大 10 Elo の劣化まで許容
  sprt:
    elo0: -5.0
    elo1: 0.0
    alpha: 0.05
    beta: 0.05
```

## 設計上のトレードオフ

### every_n_updates の設定

| 値 | LTC 頻度 | メリット | デメリット |
|:---:|:---:|:---|:---|
| 10 | 高い | 劣化を早期に検出 | チューニング全体が遅くなる |
| 50 | 中 | バランスが良い | 中程度の遅延 |
| 200 | 低い | チューニングが速い | 劣化の検出が遅れる |

### total_pairs の設定

| 値 | 精度 | 時間 | 推奨用途 |
|:---:|:---:|:---:|:---|
| 50 | 低い | 短い | スクリーニング |
| 200 | 中 | 中 | 一般的なチューニング |
| 500+ | 高い | 長い | 重要なリリース前の検証 |

## リバートの影響

LTC 回帰テストでフェイルしてリバートが発生した場合:

1. パラメータは前回のパス時点に戻る
2. SPSA 更新は次の反復から継続する
3. ゲインスケジュールは**リセットされない**（\\(k\\) は増加し続ける）

これにより、フェイル後のチューニングはより保守的（小さいステップサイズ）になり、
同じ局所最適への再突入を防ぐ効果があります。

## 実装リファレンス

| ファイル | 関数/クラス | 役割 |
|---------|----------|------|
| `_core/contexts/spsa/adapters/runtime/ltc_regression.py` | `run_ltc_regression()` | LTC 回帰テストの本体 |
| `_core/contexts/game_session/adapters/orchestration/config_spsa_models.py` | `LtcRegressionConfig` | LTC 設定 |
| `_core/contexts/game_session/adapters/orchestration/config_spsa_models.py` | `LtcPassCriteria` | パス判定基準 |

## 次に読む

→ **[分散削減テクニック](../variance-reduction/index.md)**: CRN、バッチ処理、五項分布など、推定精度を向上させる手法をまとめます。
