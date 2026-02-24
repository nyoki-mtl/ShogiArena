# ノイズと高次手法の限界

> **前提知識**: [SPSA](./index.md)、[勾配推定と摂動](./gradient.md)、[ゲインスケジュール](./gain-schedule.md)

## このページの要点

- エンジン対局の目的関数は**極端にノイジー**であり、一次の SPSA ですら「運が大きく関与する」レベル
- **二次手法**（2SPSA、Newton 法、BFGS）は Hessian 推定にさらに多くのサンプルを要求するため、ノイジーな環境では事実上使えない
- Fishtest は per-axis \\(c_i\\) パラメータを通じて**暗黙的な対角準 Newton ステップ**を実現し、明示的な Hessian 計算を回避している
- 「真の収束」は現実のリソース制約では達成不可能であり、「最適解からの指定距離以内に指定確率で到達する」戦略が実用的

## ノイズの本質

### なぜエンジンチューニングは極端にノイジーなのか

通常の最適化問題と異なり、将棋・チェスエンジンのパラメータチューニングでは、
目的関数の 1 回の「評価」が対局の勝敗という**離散的で巨大な分散を持つ確率変数**です。

```text
通常の最適化問題:
  y = f(x) + ε,  ε ~ N(0, σ²)  ← ノイズは連続的で比較的小さい

エンジンチューニング:
  y ∈ {0, 0.5, 1}（負け/引き分け/勝ち） ← 3 値の離散出力
  Var[y] ≈ p(1-p) ≈ 0.25（勝率 50% 付近で最大）
```

1 ゲームの結果から得られる勾配情報は極めて限られています。
SPSA では 2 ゲーム（\\(\theta^+\\) と \\(\theta^-\\)）の差分から \\(p\\) 次元の勾配を推定するため、
**信号対雑音比（SNR）は本質的に低い**のです。

### Fishtest 開発者の見解

Fishtest（Stockfish テスティングフレームワーク）の主要開発者である vdbergh は、
2SPSA（二次 SPSA）の導入を提案した Issue #2126 に対して、以下のように明確に否定しています。[^fishtest-2126]

> The Fishtest setting is extremely noisy. So noisy in fact that the regular 1SPSA algorithm barely works
> (a lot of luck is involved to get anything out of it). The noise would completely trash any second order algorithm.
>
> — vdbergh, fishtest Issue #2126（2024年8月3日）

また、Issue #535 における MJZ1977（SPSA の実践経験者）のコメントも、ノイズ問題の深刻さを裏付けています。[^fishtest-535]

> From my experience on SPSA, the main problem is the high level of noise in the results.
>
> — MJZ1977, fishtest Issue #535（2020年2月11日）

MJZ1977 は反復あたり最低 100 ゲームを推奨し、それ未満では収束が誤った方向に進む可能性があると警告しています。

### ノイズの定量的理解

典型的なチェスエンジンの対局で、パラメータを \\(\pm c\\) だけ変化させたときの Elo 差が約 5 Elo だとすると、
勝率の差は約 0.7% です。

\\[
\Delta W = \frac{5}{400 \ln 10} \approx 0.007
\\]

一方、1 ゲームの結果の標準偏差は約 0.5 です。したがって：

\\[
\text{SNR} = \frac{\Delta W}{\sigma / \sqrt{n}} = \frac{0.007}{0.5 / \sqrt{2}} \approx 0.02
\\]

**2 ゲームから得られる SNR はわずか 0.02** — つまり信号はノイズの 50 分の 1 しかありません。

## なぜ二次手法が使えないのか

### 二次 SPSA（2SPSA）の理論

Spall（2000）が提案した 2SPSA は、勾配と同時に Hessian 行列も同時摂動で推定するアルゴリズムです。[^spall-2000]

**1SPSA（一次）**: 2 回の評価で \\(p\\) 次元の勾配を推定
**2SPSA（二次）**: 4 回の評価で \\(p\\) 次元の勾配 + \\(p \times p\\) の Hessian を推定

学術的には 2SPSA は 1SPSA の 5〜10 倍の効率を達成できるとされています。[^zhu-spall-2002]

> 2SPSA provides a considerable reduction in the loss function value for the same number of measurements
> used in 1SPSA, with 1SPSA needing approximately five–ten times the number of function evaluations
> used by 2SPSA to reach the levels of accuracy shown.
>
> — Zhu & Spall (2002)

### エンジンチューニングで 2SPSA が破綻する理由

理論的な優位性にもかかわらず、エンジンチューニングでは以下の理由で 2SPSA は実用的ではありません。

#### 1. Hessian 推定のノイズ感度

Hessian の推定には勾配推定よりもさらに高い精度が必要です。
勾配推定が既に「運が大きく関与する」レベルのノイズに晒されている状況では、
二次の情報（曲率）を信頼性高く推定することは不可能です。

```text
一次情報（勾配）の推定:
  Var[ĝ_i] ∝ σ² / c_k²
  → 既にノイジーだが、反復平均で緩和可能

二次情報（Hessian）の推定:
  Var[Ĥ_ij] ∝ σ⁴ / c_k⁴
  → ノイズが勾配の 2 乗で増幅される
  → エンジンチューニングの σ² では壊滅的
```

#### 2. ill-conditioned Hessian の問題

2SPSA では Hessian の逆行列 \\(\hat{H}^{-1}_k\\) を計算しますが、
推定 Hessian がわずかに真の Hessian からずれただけで、
逆行列の誤差は**条件数に比例して増幅**されます。[^zhu-spall-2002]

\\[
\frac{\|\hat{H}^{-1} - H^{-1}\|}{\|H^{-1}\|} \approx \kappa(H) \cdot \frac{\|\hat{H} - H\|}{\|H\|}
\\]

エンジンのパラメータ空間では、感度が大きく異なるパラメータが混在する
（ピース評価値 vs 枝刈り閾値など）ため、Hessian の条件数は大きくなりがちです。

#### 3. 計算コストの問題

| 手法 | 評価回数/反復 | 追加の計算 |
|:---:|:---:|:---|
| 1SPSA | 2 | なし |
| 2SPSA | 4 | \\(O(p^2)\\) の Hessian 更新・逆行列計算 |

4 回の評価は、ノイジーな環境で Hessian を推定するにはまったく不十分です。

### 他の二次手法も同様

Newton 法や BFGS などの準 Newton 法は、**正確な勾配**（またはそれに近い推定）を前提としています。
SPSA 環境ではこの前提が成り立ちません。

| 手法 | 前提条件 | エンジンチューニングでの状況 |
|:---|:---|:---|
| Newton 法 | 正確な勾配と Hessian | 両方とも推定不可能 |
| BFGS | 正確な勾配 | 勾配推定が極めてノイジー |
| L-BFGS | 正確な勾配 | 同上 |
| Adam | ノイジーな勾配に対応 | Texel Tuning[^texel]では有効。ゲームプレイベースでは勾配のノイズが大きすぎる |

Adam は深層学習で広く成功していますが、
深層学習のミニバッチ勾配と SPSA の 2 ゲーム勾配推定ではノイズのレベルが根本的に異なります。

```text
深層学習: ミニバッチサイズ 32〜256 サンプルから勾配を推定 → SNR は比較的高い
SPSA: 2 ゲーム（実質 1 サンプル）から p 次元の勾配を推定 → SNR ≈ 0.02
```

実際、チェスエンジン開発コミュニティでは以下の区別が明確にされています。[^talkchess-hybrid]

- **Texel Tuning**（棋譜データベースからの教師あり学習）: 解析的勾配が計算可能 → Adam、Adagrad が有効
- **ゲームプレイベースの SPSA**: 勾配は対局結果からのみ推定 → 適応的学習率は勾配推定の根本的問題を解決しない

> Arasan uses ADAM and Ethereal uses Adagrad with standard gradient descent implementation, not SPSA.
>
> — jdart, TalkChess

つまり、Adam が成功しているのは SPSA の勾配推定と組み合わせた場合ではなく、
**バックプロパゲーション的に正確な勾配が得られる場合のみ**です。

## Fishtest の実用的解法：暗黙的対角準 Newton

### per-axis \\(c_i\\) による曲率の吸収

Fishtest は明示的な Hessian 計算の代わりに、パラメータごとの摂動スケール \\(c_i\\) を
ユーザーが設定することで、**暗黙的に曲率情報を組み込む**設計を採用しています。

Fishtest の公式ドキュメントでは、この設計の理論的根拠を以下のように説明しています。[^fishtest-math]

> Per-axis \\(c_i\\) encodes axis scale/curvature, yielding diagonal quasi-Newton steps in \\(\theta\\)
> even while tuning with a global learning rate.
>
> — Fishtest Mathematics Documentation

具体的には：

1. ユーザーが \\(c_i\\) を「\\(\theta_i \pm c_i \Delta_i\\) が数 Elo の差を生む」ように設定
2. 正規化パラメータ \\(\varphi_i = \theta_i / c_i\\) では、すべてのパラメータが同じスケールになる
3. \\(\theta\\) 空間に戻すと \\(\Delta\theta_i = r_i \cdot c_i \cdot g_{\varphi_i}\\) — **平坦な軸では大きな更新、急峻な軸では小さな更新**

```text
        二次手法の本来の目的:
        Δθ = -H⁻¹ ∇L   ← 曲率に応じてステップを調整

        Fishtest の暗黙的実現:
        Δθ_i = r · c_i · g_φ_i
              ≈ r · (1/√H_ii) · ∂L/∂θ_i   ← 対角近似
```

この方法は以下の利点があります。

- **追加のゲームが不要**: Hessian 推定用の追加ゲームを実行しない
- **ノイズ耐性**: ユーザーの事前知識で曲率を「一度だけ」設定するため、ノイジーな推定に依存しない
- **計算オーバーヘッドなし**: \\(O(p^2)\\) の行列演算が不要

### ShogiArena での対応

ShogiArena では、`ParamEntry` の `step` と `delta` パラメータがこの役割を果たしています。

```text
step: 摂動の大きさ（Fishtest の c_i に相当）
  → パラメータの感度に応じて設定

delta: 学習率スケール
  → パラメータごとの更新速度を調整

これらを適切に設定することで、暗黙的に曲率情報を組み込める
```

## 収束の限界

### 「真の収束」は達成不可能

Stockfish のチューニング手法の原案者である Joona Kiiski（zamar）は、
収束の根本的な限界について以下のように述べています。[^kiiski]

> After a while important variables stop approaching optimal values and "random walk" takes over
> and the strength starts to decrease. So the method doesn't converge and it needs to be stopped
> at "suitable moment".
>
> — Joona Kiiski, Stockfish's Tuning Method（TalkChess, 2011年）

この現象は以下のメカニズムで発生します。

```text
チューニング初期:
  ├─ 感度の高いパラメータ → 最適値に近づく ✓
  └─ 感度の低いパラメータ → ランダムウォーク ×

チューニング中期:
  ├─ 感度の高いパラメータ → 最適値付近で安定
  └─ 感度の低いパラメータ → まだランダムウォーク

チューニング後期:
  ├─ 感度の高いパラメータ → ランダムウォークに転落
  └─ 感度の低いパラメータ → ランダムウォーク継続
  全体 → 強度低下！停止すべきタイミング
```

### 精度・信頼度フレームワーク

vdbergh の SPSA シミュレータ（spsa_simul）は、この問題に対して
「収束を目指さない」アプローチを提案しています。[^spsa-simul]

> Due to resource constraints in chess tuning, no reasonable form of convergence is ever achievable,
> so the best approach is to plan for ending up within a specified distance from the optimum
> with a specified probability.
>
> — vdbergh, spsa_simul README

- **精度**（precision）: 最適解からの目標 Elo 距離（デフォルト: 0.5 Elo）
- **信頼度**（confidence）: 目標達成の確率（デフォルト: 95%）

このフレームワークでは、固定ゲイン（\\(\alpha = \gamma = 0\\)）を使用し、
漸近的収束ではなく有限サンプルでの性能を最適化します。
ShogiArena のデフォルト設定（\\(\alpha = \gamma = 0\\)）もこの考え方に基づいています。

### パラメータ数とスケーリング

パラメータ数が増えると、必要な計算量は急激に増大します。

| パラメータ数 | 目安のゲーム数 | 備考 |
|:---:|:---:|:---|
| 1〜5 | \\(O(100\text{K})\\) | 現実的に収束可能 |
| 10〜30 | \\(O(1\text{M})\\) | vondele の nevergrad4sf での経験値[^nevergrad4sf] |
| 64+ | \\(O(10\text{M}+)\\) | 200,000 反復では不十分という報告あり |

vondele（Stockfish メンテナー）は nevergrad4sf で以下のように述べています。

> Count on O(1M) games for convergence, even with few parameters.
>
> — vondele, nevergrad4sf README

### 変数選択の重要性

パラメータ数の問題に対する Kiiski の解決策は「メタパラメータ」の導入でした。

> Variable selection extremely important. Tuning each piece-square value separately doomed to fail —
> changing single value changes strength minimally. Instead using ampli-bias knobs proved successful.
>
> — Joona Kiiski, TalkChess

個々のパラメータを直接チューニングする代わりに、
複数のパラメータに影響を与える少数の「制御ノブ」（ampli-bias 変換）を使うことで、
効果的にパラメータ空間の次元を削減しています。

## 代替手法との比較

### Rémi Coulom（CLOP 開発者）の評価

> SPSA has potential close to CLOP in performance, but main weakness is difficulty choosing
> good meta-parameters. In practice, impossible finding optimal SPSA meta-parameters.
>
> — Rémi Coulom, TalkChess（2013年）

### 手法の位置付け

| 手法 | 局所/大域 | ノイズ耐性 | メタパラメータ調整 | 備考 |
|:---|:---:|:---:|:---:|:---|
| SPSA | 局所 | 中 | 困難 | 実績豊富、fishtest で実用 |
| BSPSA | 局所 | 高 | 中 | ベイズ推定で SPSA を 20〜40% 改善[^bspsa] |
| CLOP | 大域 | 高 | 容易 | ベイズ最適化ベース |
| nevergrad (TBPSA) | 大域 | 高 | 容易 | vondele が Stockfish に使用[^nevergrad4sf] |
| Hyperopt (TPE) | 大域 | 高 | 中 | 次元の呪いの影響を受ける[^fishtest-774] |
| Texel Tuning + Adam | 局所 | 高 | 容易 | 解析的勾配が必要（HCE のみ）[^texel] |

SPSA の最大の強みは**分散テスティング環境との親和性**です。
Fishtest のように多数のワーカーが非同期で並列実行する環境では、
各ワーカーが独立に摂動ゲームを実行できる SPSA の単純さが有利です。

## 実践的な教訓

### ck の設定が最も重要

> If after a few thousand games the values are barely changing at all, the tuning run is useless
> and should be stopped. This usually happens when the ck value is too low for the parameter changes
> to influence the tuning result more than random noise.
>
> — Fishtest Wiki, Creating my first test

**ck が小さすぎる場合**: パラメータ変化がノイズに埋もれ、更新がランダムウォークになる
**ck が大きすぎる場合**: 線形近似が破綻し、勾配推定にバイアスが生じる

### チューニングの実用的な進め方

Fishtest コミュニティでの経験則[^fishtest-535]：

1. **少数の重要パラメータから始める**: 全パラメータを一度にチューニングしない
2. **ck は「数 Elo の差を生む」レベルに設定**: 小さすぎるとノイズに埋もれる
3. **進捗を監視する**: 数千ゲーム後にパラメータが動いていなければ、ck を上げるか中止
4. **LTC 回帰テストで検証**: STC でのチューニング結果が LTC でも有効か確認
5. **「適切なタイミング」で停止する**: 収束を待たず、強度のピークで停止

## 参考文献

[^fishtest-2126]: [Fast 2SPSA - Issue #2126](https://github.com/official-stockfish/fishtest/issues/2126) — vdbergh による二次手法の否定。Fishtest のノイズレベルでは一次 SPSA ですら「運が大きく関与する」と明言。

[^fishtest-535]: [SPSA improvements \[RFC\] - Issue #535](https://github.com/official-stockfish/fishtest/issues/535) — 427 コメントに及ぶ SPSA 改善の議論。ppigazzini, vdbergh, MJZ1977, vondele らが参加。

[^fishtest-774]: [Stochastic optimization for parameters - Issue #774](https://github.com/glinscott/fishtest/issues/774) — SPSA、Hyperopt、chess-tuning-tools の比較議論。

[^fishtest-math]: [Fishtest Mathematics Documentation](https://official-stockfish.github.io/docs/fishtest-wiki/Fishtest-Mathematics.html) — per-axis \\(c_i\\) による対角準 Newton ステップの公式解説。

[^spall-2000]: James C. Spall (2000). "Adaptive Stochastic Approximation by the Simultaneous Perturbation Method". *IEEE Transactions on Automatic Control*, 45(10), pp. 1839-1853.

[^zhu-spall-2002]: J. Zhu & J. C. Spall (2002). "A Modified Second-Order SPSA Optimization Algorithm for Finite Samples". *International Journal of Adaptive Control and Signal Processing*, 16(5), pp. 397-409.

[^kiiski]: [Stockfish's Tuning Method](https://www.chessprogramming.org/Stockfish%27s_Tuning_Method) — Joona Kiiski によるチューニング手法の原案。TalkChess での議論を基にまとめたもの。

[^spsa-simul]: [vdbergh/spsa_simul](https://github.com/vdbergh/spsa_simul) — チェス版 SPSA のマルチスレッドシミュレータ。精度・信頼度フレームワークの理論的基盤を提供。

[^nevergrad4sf]: [vondele/nevergrad4sf](https://github.com/vondele/nevergrad4sf) — Facebook Research の nevergrad ライブラリを使った Stockfish パラメータ最適化。TBPSA 法を使用。

[^bspsa]: N. Atkinson et al. (2022). "Bayesian Statistics Approach to Chess Engines Optimization". [arXiv:2205.15602](https://arxiv.org/abs/2205.15602) — SPSA のベイズ推定版。シミュレーションで 40% 以上、実際の Stockfish チューニングで約 20% の改善を報告。

[^talkchess-hybrid]: [A hybrid of SPSA and local optimization](https://talkchess.com/viewtopic.php?t=77420) — SPSA と正確な勾配計算のハイブリッド手法に関する議論。Adam/Adagrad は SPSA の勾配推定問題を解決しないという知見。

[^texel]: Texel Tuning は評価関数の重みを棋譜データベースから教師あり学習で最適化する手法。目的関数（MSE）が解析的に微分可能なため、Adam 等の勾配法が直接適用可能。ゲームプレイベースの SPSA とは異なるコンテキスト。

## 次に読む

→ **[LTC 回帰テスト](./ltc-regression.md)**: 「適切なタイミング」の判定を自動化する手法。STC でのチューニング結果が LTC で劣化していないかを定期検証します。
