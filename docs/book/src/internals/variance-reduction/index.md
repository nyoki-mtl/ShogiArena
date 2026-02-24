# 分散削減テクニック

> **前提知識**: [SPRT](../sprt/index.md)、[SPSA](../spsa/index.md)

## このページの要点

- エンジンテストの結果は確率的であり、推定の分散を削減することで少ないゲーム数で高精度な結論を得られる
- **CRN（共通乱数法）**: 同じ開始局面を使って比較の精度を向上させる
- **ペアゲーム**: 先後を入れ替えた 2 局をセットにし、先手有利バイアスを除去する
- **バッチ処理**: 複数ゲームの結果を平均して勾配推定の分散を削減する
- これらの手法は直交しており、組み合わせて使用可能

## 分散が問題になる理由

エンジン対局の結果は本質的に確率的です。
同じエンジンペアでも、対局ごとに異なる結果（勝ち/引き分け/負け）になります。

この分散が大きいと:

- **SPRT**: 判定に到達するまでのゲーム数が増加する
- **SPSA**: 勾配推定がノイズに埋もれ、最適化が不安定になる
- **Elo 推定**: 信頼区間が広くなり、実用的な判断が困難になる

## CRN（共通乱数法）

### 原理

**Common Random Numbers (CRN)** は、比較対象の 2 つのシステムに**同じ乱数列**（＝同じ開始局面）を与えることで、共通の変動要因を相殺する手法です。

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

分散削減の原理:

\\[
\text{Var}[s^+ - s^-] = \text{Var}[s^+] + \text{Var}[s^-] - 2\text{Cov}[s^+, s^-]
\\]

CRN を使うと \\(\text{Cov}[s^+, s^-] > 0\\)（同じ局面では似た結果になりやすい）となり、
差の分散が削減されます。

### ShogiArena の実装

```python
if self.config.crn_enabled:
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

先手勝率を \\(p_b\\)（先手有利バイアス）、Engine A の実力による勝率を \\(p_a\\) とすると:

- 独立ゲーム: \\(\text{Var} \propto p_b(1-p_b) + p_a(1-p_a)\\)
- ペアゲーム: \\(\text{Var} \propto p_a(1-p_a)\\)（\\(p_b\\) の項が消える）

先後バイアス分の分散が完全に除去されます。

### 五項分布との関係

ペアゲームの結果は [五項分布](../sprt/pentanomial.md) でモデル化できます。
5 カテゴリ（0.0, 0.5, 1.0, 1.5, 2.0）による分析は、三項分布（勝/引/負）よりも情報量が多く、
SPRT の判定に必要なゲーム数を 20-40% 削減できます。

## バッチ処理

### 原理

SPSA の 1 回の更新で、同じ摂動に対して複数のゲームを実行し、スコアを平均します。

\\[
\bar{s}^+ = \frac{1}{B} \sum_{b=1}^{B} s^+_b \qquad \bar{s}^- = \frac{1}{B} \sum_{b=1}^{B} s^-_b
\\]

### 分散削減効果

\\[
\text{Var}[\bar{s}^+ - \bar{s}^-] = \frac{1}{B} \text{Var}[s^+ - s^-]
\\]

バッチサイズ \\(B\\) に反比例して分散が減少します。

### 実装

```python
batch_size = self.config.update_batch_size or 1
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

バッチサイズを \\(B\\) にすると:

- **分散**: \\(1/B\\) に削減
- **1 更新あたりのゲーム数**: \\(2B\\) に増加
- **総ゲーム数**: \\(N \cdot 2B\\)（更新回数 \\(N\\) が同じ場合）

重要なのは、分散削減によって**同じ総ゲーム数でも最適化の精度が向上する**ことです。
ただし、更新回数 \\(N\\) が減ることで探索の多様性が犠牲になる場合もあります。

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

1 回の更新あたりのゲーム数: \\(2 \times 2 \times B = 4B\\) 局

### 推奨設定

```yaml
# 分散削減の設定
crn_enabled: true           # CRN を有効化
update_batch_size: 4         # バッチサイズ 4

# 並列実行
inflight_factor: 8           # 同時ゲーム数の乗数
```

## 効果の比較

以下は概念的な比較です（実際の効果は問題に依存します）。

| 手法 | 分散削減率 | 追加コスト | 実装の複雑さ |
|:---|:---:|:---:|:---:|
| CRN | 30-50% | なし | 低い |
| ペアゲーム | 20-40% | 2倍 | 低い |
| バッチ (B=4) | 75% | 4倍 | 低い |
| 全手法併用 | 80-90% | 8倍 | 中 |

> **注**: CRN は追加コストなしで分散を削減できるため、常に有効にすることを推奨します。

## 実装リファレンス

| ファイル | 設定キー/関数 | 役割 |
|---------|-------------|------|
| `arena/configs/spsa.py` | `crn_enabled` | CRN の有効/無効 |
| `arena/configs/spsa.py` | `update_batch_size` | バッチサイズ |
| `arena/orchestrators/spsa_orchestrator.py` | `_run_game_pair()` | ペアゲームの実行 |
| `arena/services/statistics/pentanomial.py` | `compute_pentanomial()` | 五項分布の計算 |

## 参考文献

- Asmussen, S. and Glynn, P. (2007). *Stochastic Simulation: Algorithms and Analysis*. Springer. — CRN を含むモンテカルロ法の分散削減手法の体系的な解説
- Spall, J. C. (2003). *Introduction to Stochastic Search and Optimization*. Wiley. — SPSA におけるバッチ処理と分散削減の理論

## 次に読む

これで技術解説章は完了です。以下のパスで理解を深めることを推奨します:

- [SPRT の設定と解釈](../sprt/index.md) に戻り、設定パラメータの選び方を復習する
- [SPSA のゲインスケジュール](../spsa/gain-schedule.md) で最適化の微調整方法を学ぶ
- [ユーザーガイド: SPSA チューニング](../../user-guide/spsa.md) で実践的な使い方を確認する
