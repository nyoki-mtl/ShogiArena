# 分散削減テクニック

> **前提知識**：[SPRT](../sprt/index.md)、[SPSA](../spsa/index.md)

## このページの要点

- エンジンテストの結果は確率的であり、推定の分散を削減することで少ない対局数で高精度な結論を得られる
- **CRN（共通乱数法）**：同じ開始局面を使って比較の精度を向上させる
- **ペアゲーム**：先後を入れ替えた 2 局をセットにし、先手有利バイアスを除去する
- **バッチ処理**：複数局の結果を平均して勾配推定の分散を削減する
- これらの手法は直交しており、組み合わせて使用可能

## 分散が問題になる理由

エンジン対局の結果は本質的に確率的です。
同じエンジンペアでも、対局ごとに勝ち、引き分け、負けのいずれにもなります。

この揺らぎは、本書で扱う三つの推定すべてに跳ね返ります。

- **SPRT**：判定に到達するまでの対局数が増加する
- **SPSA**：勾配推定がノイズに埋もれ、最適化が不安定になる
- **Elo 推定**：信頼区間が広くなり、実用的な判断が困難になる

## CRN（共通乱数法）

### 原理

2 つのシステムを比べるとき、結果の差にはシステムの違いだけでなく、実験条件の当たり外れも混ざります。
**Common Random Numbers（CRN）** は、両者に同じ乱数列（ここでは同じ開始局面）を与えて、この共通の変動要因を相殺する手法です。

```text
CRN なし:
  θ⁺ → 局面 A で対局 → スコア s⁺
  θ⁻ → 局面 B で対局 → スコア s⁻
  → 局面 A と B の難易度の差がノイズに加算

CRN あり:
  θ⁺ → 局面 A で対局 → スコア s⁺
  θ⁻ → 局面 A で対局 → スコア s⁻
  → 局面の影響がキャンセルされ、純粋なパラメータの差を測定
```

### 数学的背景

2 つのスコアの差の分散は、次のように分解できます。

\\[
\text{Var}[s^+ - s^-] = \text{Var}[s^+] + \text{Var}[s^-] - 2\text{Cov}[s^+, s^-]
\\]

同じ局面を使えば、局面自体の難易度が両者のスコアを同じ向きに動かすため \\(\text{Cov}[s^+, s^-] > 0\\) となります。
共分散の項が引かれるぶんだけ、差の分散が小さくなります。

### ShogiArena の実装

```python
if self.config.is_crn_enabled:
    # 同じ SFEN を θ⁺ と θ⁻ の両方に使用
    sfen = sfens[rng.randrange(len(sfens))]
    s_plus  = await self._run_game_pair(sfen, tuned_plus, ...)
    s_minus = await self._run_game_pair(sfen, tuned_minus, ...)
else:
    # 異なる SFEN を使用（分散が大きい）
    sfen_plus  = sfens[rng.randrange(len(sfens))]
    sfen_minus = sfens[rng.randrange(len(sfens))]
    s_plus  = await self._run_game_pair(sfen_plus, tuned_plus, ...)
    s_minus = await self._run_game_pair(sfen_minus, tuned_minus, ...)
```

### CRN を使うべき場合

| 状況 | CRN | 理由 |
|:---|:---:|:---|
| SPSA 勾配推定 | ✓ | s⁺ - s⁻ の精度が直接的に影響 |
| SPRT エンジン比較 | 推奨 | ペアゲームとの組み合わせが効果的 |
| 多様な局面での評価 | ✗ | 局面の多様性を確保したい場合は CRN なし |

## ペアゲーム（先後入れ替え）

### 原理

将棋（およびチェス）では先手が有利です。この先後バイアスは、エンジンの実力差とは無関係のノイズです。

ペアゲームでは、同じ開始局面で先後を入れ替えた 2 局をセットにします。

```text
ペア:
  Game 1: Engine A (先手) vs Engine B (後手) → 結果 r₁
  Game 2: Engine A (後手) vs Engine B (先手) → 結果 r₂

ペアスコア = r₁ + r₂  (先後バイアスが相殺)
```

### 分散削減効果

先手勝率を \\(p_b\\)（先手有利バイアス）、Engine A の実力による勝率を \\(p_a\\) とすると、次のようになります。

- 独立した 2 局：\\(\text{Var} \propto p_b(1-p_b) + p_a(1-p_a)\\)
- ペアゲーム：\\(\text{Var} \propto p_a(1-p_a)\\)（\\(p_b\\) の項が消える）

ペアの中で先手番が両エンジンに 1 回ずつ割り当てられるため、先後バイアス由来の分散が残りません。

### 五項分布との関係

ペアゲームの結果は [五項分布](../sprt/pentanomial.md) でモデル化できます。
5 カテゴリ（0.0, 0.5, 1.0, 1.5, 2.0）による分析は、三項分布（勝/引/負）よりも情報量が多く、
SPRT の判定に必要な対局数を 20-40% 削減できます。

## バッチ処理

### 原理

SPSA の 1 回の更新で、同じ摂動に対して複数の対局を実行し、スコアを平均します。

\\[
\bar{s}^+ = \frac{1}{B} \sum_{b=1}^{B} s^+\_b \qquad \bar{s}^- = \frac{1}{B} \sum_{b=1}^{B} s^-\_b
\\]

### 分散削減効果

\\[
\text{Var}[\bar{s}^+ - \bar{s}^-] = \frac{1}{B} \text{Var}[s^+ - s^-]
\\]

バッチサイズ \\(B\\) に反比例して分散が減少します。

### 実装

```python
batch_size = self.config.pairs_per_update or 1
total_s_plus = 0.0
total_s_minus = 0.0

for batch_idx in range(batch_size):
    s_plus_i, s_minus_i = await run_batch_item(batch_idx)
    total_s_plus += s_plus_i
    total_s_minus += s_minus_i

s_plus = total_s_plus / batch_size
s_minus = total_s_minus / batch_size
```

### コストと利益のトレードオフ

バッチサイズを \\(B\\) にすると、次のようになります。

- **分散**：\\(1/B\\) に削減
- **1 更新あたりの対局数**：\\(2B\\) に増加
- **総対局数**：\\(N \cdot 2B\\)（更新回数 \\(N\\) が同じ場合）

総対局数を固定するなら、バッチサイズを上げたぶんだけ更新回数 \\(N\\) は減ります。
1 回の更新がノイズに振り回されにくくなる代わりに、パラメータ空間を動き回る回数そのものは減るという交換です。

## 手法の組み合わせ

これらの手法は直交しており、すべて同時に使用できます。

```text
1 回の SPSA 更新:
  ┌─────────────────────────────────────────────┐
  │ CRN: 同じ SFEN を θ⁺ と θ⁻ に使用          │
  │   ┌────────────────────────────────────────┐ │
  │   │ ペアゲーム: 先後入れ替え 2 局           │ │
  │   │   ┌───────────────────────────────────┐│ │
  │   │   │ バッチ: B 回繰り返して平均         ││ │
  │   │   └───────────────────────────────────┘│ │
  │   └────────────────────────────────────────┘ │
  └─────────────────────────────────────────────┘
```

1 回の更新あたりの対局数は \\(2 \times 2 \times B = 4B\\) 局になります。

### 推奨設定

```yaml
spsa:
  pairs_per_update: 4        # 1 回の更新に使うペア数（バッチサイズ）
  inflight_factor: 8         # 同時進行させる対局数の乗数
  variants:
    crn: true                # CRN を有効化
```

## 効果の比較

以下は概念的な比較です（実際の効果は問題に依存します）。

| 手法 | 分散削減率 | 追加コスト | 実装の複雑さ |
|:---|:---:|:---:|:---:|
| CRN | 30-50% | なし | 低い |
| ペアゲーム | 20-40% | 2倍 | 低い |
| バッチ (B=4) | 75% | 4倍 | 低い |
| 全手法併用 | 80-90% | 8倍 | 中 |

> **注**：CRN は追加コストなしで分散を削減できるため、常に有効にすることを推奨します。

## 実装リファレンス

| ファイル | 設定キー/関数 | 役割 |
|---------|-------------|------|
| `_core/contexts/game_session/adapters/orchestration/config_spsa_models.py` | `variants.crn`（`is_crn_enabled`） | CRN の有効と無効 |
| `_core/contexts/game_session/adapters/orchestration/config_spsa_models.py` | `pairs_per_update` | 1 回の更新に使うペア数 |
| `_core/contexts/spsa/adapters/orchestrator.py` | `SpsaOrchestrator` | ペアゲーム実行を含む orchestration |
| `_core/shared/kernel/statistics/pentanomial.py` | `compute_pentanomial()` | 五項分布の計算 |

## 参考文献

- Asmussen, S. and Glynn, P. (2007). *Stochastic Simulation: Algorithms and Analysis*. Springer. — CRN を含むモンテカルロ法の分散削減手法の体系的な解説
- Spall, J. C. (2003). *Introduction to Stochastic Search and Optimization*. Wiley. — SPSA におけるバッチ処理と分散削減の理論

## 次に読む

技術解説章はここまでです。続きの読み方は次の三つがあります。

- [SPRT の設定と解釈](../sprt/index.md)：設定パラメータの選び方
- [SPSA のゲインスケジュール](../spsa/gain-schedule.md)：最適化の微調整
- [ユーザーガイド: SPSA チューニング](../../user-guide/spsa.md)：実際の設定と実行手順
