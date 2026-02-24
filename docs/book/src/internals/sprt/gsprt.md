# GSPRT（一般化逐次確率比検定）

> **前提知識**: [SPRT](./index.md)、[対数尤度比（LLR）](./llr.md)

## このページの要点

- GSPRT（Generalized SPRT）は、仮説中の**未知パラメータを最尤推定量（MLE）で置き換えた** SPRT の一般化
- 標準的な SPRT は仮説を完全に特定する必要があるが、GSPRT は部分的な仮説（スコアのみ指定）で動作する
- 誤り率が 0 に近づくとき、GSPRT は標準 SPRT と漸近的に同じ振る舞いをする
- Fishtest が採用している「SPRT」は実際には GSPRT であり、ShogiArena の SPRT 実装の理論的基盤でもある

## 標準 SPRT の限界

### 単純仮説と複合仮説

標準的な SPRT（Wald, 1945）は**単純仮説**を前提とします。
つまり、H0 と H1 のもとでの確率分布が完全に特定されている必要があります。

```text
単純仮説（標準 SPRT）:
  H0: 分布は P0 である（完全に特定）
  H1: 分布は P1 である（完全に特定）

複合仮説（GSPRT）:
  H0: スコアの期待値は s0 である（分布の他のパラメータは不明）
  H1: スコアの期待値は s1 である（分布の他のパラメータは不明）
```

エンジンテストでは、仮説は「スコアの期待値が \\(s_0\\)（または \\(s_1\\)）である」という形ですが、引き分け率などのパラメータは未知です。このため、単純仮説を設定できず、標準 SPRT を直接適用できません。

### 素朴なアプローチの問題

素朴な解決策として、引き分け率をデータから推定して単純仮説を構成する方法があります（Fishtest のレガシー BayesElo モデル）。しかし、これにはバイアスが生じる可能性があります。

GSPRT はこの問題をエレガントに解決します。

## GSPRT の定義

### 一般化対数尤度比

GSPRT では、仮説中の未知パラメータを**制約付き最尤推定量**で置き換えます。[^gsprt-paper]

\\[
\Lambda_n = \frac{\sup_{\theta \in \Theta_1} L(\theta; X_1, \ldots, X_n)}{\sup_{\theta \in \Theta_0} L(\theta; X_1, \ldots, X_n)}
\\]

ここで：
- \\(L(\theta; X_1, \ldots, X_n)\\) はデータの尤度関数
- \\(\Theta_0, \Theta_1\\) はそれぞれ H0, H1 のもとでのパラメータ空間
- \\(\sup\\) は制約のもとでの最尤推定（MLE）に対応

対数を取ると：

\\[
\text{LLR}_n = \ln \Lambda_n = \ell(\hat{\theta}_1) - \ell(\hat{\theta}_0)
\\]

ここで \\(\hat{\theta}_0, \hat{\theta}_1\\) はそれぞれ \\(\Theta_0, \Theta_1\\) 上の制約付き MLE、\\(\ell\\) は対数尤度関数です。

### エンジンテストでの適用

エンジンテストの文脈では：

- **パラメータ空間**: 多項分布のパラメータ（勝率、引き分け率、負け率）
- **制約**: 期待スコアが \\(s_0\\)（H0）または \\(s_1\\)（H1）であること
- **MLE**: 観測された頻度分布のもとで、指定されたスコアを持つ多項分布のうち最も尤もらしいもの

## 多項分布の制約付き MLE

### 定式化

観測された経験分布を \\(\hat{p} = (\hat{p}_0, \hat{p}_1, \ldots, \hat{p}_{N-1})\\) とします（\\(N\\) はカテゴリ数）。
各カテゴリの値を \\(a_i = i / (N-1)\\)（三項: \\(i \in \{0, 1, 2\}\\)、五項: \\(i \in \{0, 1, 2, 3, 4\}\\)）とします。

「期待値が \\(s\\) である多項分布のうち、経験分布に対する尤度を最大化する分布」を求めます。

### 世俗方程式（Secular Equation）

制約付き MLE は、以下の**世俗方程式**を解くことで得られます：[^mle-multinomial]

\\[
\sum_{i} \frac{\hat{p}_i \cdot (a_i - s)}{1 + x \cdot (a_i - s)} = 0
\\]

この方程式を \\(x\\) について解き、MLE 分布を構成します：

\\[
p_i^{\text{MLE}} = \frac{\hat{p}_i}{1 + x \cdot (a_i - s)}
\\]

```python
def MLE_expected(pdfhat, s):
    """期待値が s である制約のもとで、
    経験分布 pdfhat に対する MLE 分布を計算する。"""
    pdf1 = [(ai - s, pi) for ai, pi in pdfhat]
    x = secular(pdf1)  # 世俗方程式を解く
    pdf_MLE = [(ai, pi / (1 + x * (ai - s))) for ai, pi in pdfhat]
    return pdf_MLE


def secular(pdf):
    """世俗方程式 sum(pi * ai / (1 + x * ai)) = 0 を解く。"""
    v = min(ai for ai, pi in pdf)
    w = max(ai for ai, pi in pdf)
    # x の探索範囲: (-1/w, -1/v)
    lower_bound = -1 / w
    upper_bound = -1 / v
    x = brentq(lambda x: sum(pi * ai / (1 + x * ai) for ai, pi in pdf),
               lower_bound + epsilon, upper_bound - epsilon)
    return x
```

### 直感的理解

世俗方程式は、「観測データに最もフィットしつつ、期待値が \\(s\\) であるという制約を満たす分布」を見つけるためのラグランジュ乗数法の結果です。

パラメータ \\(x\\) はラグランジュ乗数に対応し、\\(x = 0\\) のとき MLE は経験分布そのものになります（制約なしの場合）。

## GSPRT-LLR の計算

### 正確な公式

H0 (スコア = \\(s_0\\)) と H1 (スコア = \\(s_1\\)) に対する GSPRT-LLR は：

\\[
\text{LLR} = N \sum_{i} \hat{p}_i \cdot \ln\frac{p_{1,i}^{\text{MLE}}}{p_{0,i}^{\text{MLE}}}
\\]

ここで \\(p_{0,i}^{\text{MLE}}\\), \\(p_{1,i}^{\text{MLE}}\\) はそれぞれ H0, H1 のもとでの制約付き MLE です。

```python
def LLR(pdf, s0, s1):
    """GSPRT の一般化対数尤度比（N で割ったもの）を計算する。"""
    pdf0 = MLE_expected(pdf, s0)
    pdf1 = MLE_expected(pdf, s1)
    return sum(pi * (math.log(p1i) - math.log(p0i))
               for (_, pi), (_, p0i), (_, p1i) in zip(pdf, pdf0, pdf1))
```

### 2 次近似公式

GSPRT-LLR の効率的な近似式（ブラウン運動近似）：[^gsprt-approx]

\\[
\text{LLR} \approx \frac{(s_1 - s_0)(2\hat{s} - s_0 - s_1)}{2 \hat{\sigma}^2 / N}
\\]

ここで \\(\hat{s}\\) は観測スコア、\\(\hat{\sigma}^2\\) はスコアの分散、\\(N\\) はサンプル数です。

等価な表現：

\\[
\text{LLR} \approx \frac{N(s_1 - s_0)(2\hat{s} - s_0 - s_1)}{2 \hat{\sigma}^2}
\\]

```python
def LLR_alt2(pdf, s0, s1):
    """2 次近似による GSPRT-LLR（N で割ったもの）。"""
    s, var = stats(pdf)
    return (s1 - s0) * (2 * s - s0 - s1) / var / 2.0
```

この近似は、\\(\hat{s}\\) が \\(s_0\\) と \\(s_1\\) の間にあるとき特に精度が高いです。

### もう一つの近似（分散比）

\\[
\text{LLR} \approx \frac{N}{2} \ln\frac{r_0}{r_1}
\\]

ここで \\(r_j = \sum_i \hat{p}_i (a_i - s_j)^2\\) は仮説 \\(s_j\\) のもとでの残差 2 乗和です。

```python
def LLR_alt(pdf, s0, s1):
    """分散比近似による GSPRT-LLR（N で割ったもの）。"""
    r0, r1 = [sum(prob * (value - s) ** 2 for value, prob in pdf)
              for s in (s0, s1)]
    return 0.5 * math.log(r0 / r1)
```

## ブラウン運動近似

### GSPRT をブラウン運動として扱う

GSPRT の LLR 過程は、十分なサンプル数のもとでは**ドリフト付きブラウン運動**で近似できます。[^brownian-approx]

\\[
\text{LLR}_n \approx \mu \cdot n + \sigma_{\text{LLR}} \cdot W_n
\\]

ここで \\(W_n\\) は標準ウィーナー過程、\\(\mu\\) と \\(\sigma_{\text{LLR}}^2\\) はそれぞれ LLR ジャンプのドリフトと分散です。

### ドリフトと分散

真のスコアが \\(s\\) のとき、ブラウン運動近似でのドリフトと分散：

\\[
\mu = \frac{(s - (s_0 + s_1)/2)(s_1 - s_0)}{v}, \quad \sigma^2 = \frac{(s_1 - s_0)^2}{v}
\\]

ここで \\(v\\) はスコアの分散です。

### 期待ゲーム数の公式

ブラウン運動近似を用いると、SPRT テストの期待ゲーム数を計算できます。
これは [SPRT calculator](https://tests.stockfishchess.org/sprt_calc) の基礎となっています。

## オーバーシュート補正

### 問題

SPRT の理論的な境界値は、LLR が**連続的に**変化することを前提としています。
しかし実際には LLR は離散的（1 ゲームまたは 1 ペアごと）に更新されるため、
境界値を正確に踏むのではなく、**飛び越える**（オーバーシュートする）ことがあります。

```text
LLR
 +2.94 ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ 理論的境界
       │                 ╱╱
       │              ╱╱╱
       │           ╱╱╱     ← オーバーシュート！LLR が +3.1 で判定
       │        ╱╱╱
 +2.00 ─ ─ ╱╱╱ ─ ─ ─ ─ ─ ─
       └──────────────────→ ゲーム数
```

オーバーシュートが大きいと、実際の誤り率が設定値（α, β）より大きくなります。

### Siegmund のオーバーシュート補正

Fishtest は、Siegmund (1985) の理論に基づく**動的オーバーシュート補正**を実装しています。[^siegmund]

LLR が境界付近にあるとき、オーバーシュートの期待値を推定し、実効的な境界を調整します：

\\[
\text{lower}_{\text{eff}} = \text{lower} + o_0, \quad \text{upper}_{\text{eff}} = \text{upper} - o_1
\\]

オーバーシュートの推定：

\\[
o_0 = -\frac{\sum \delta_0^2}{2 \sum \delta_0}, \quad o_1 = \frac{\sum \delta_1^2}{2 \sum \delta_1}
\\]

ここで \\(\delta_0\\) は LLR が下降して新しい最小値を記録したときのジャンプ、\\(\delta_1\\) は上昇して新しい最大値を記録したときのジャンプです。

```python
# Fishtest のオーバーシュート補正
if LLR_ < o["ref0"]:
    delta = LLR_ - o["ref0"]
    o["m0"] += delta       # δ の累積和
    o["sq0"] += delta ** 2  # δ² の累積和
    o["ref0"] = LLR_

if LLR_ > o["ref1"]:
    delta = LLR_ - o["ref1"]
    o["m1"] += delta
    o["sq1"] += delta ** 2
    o["ref1"] = LLR_

# 実効的な境界の計算
o0 = -o["sq0"] / o["m0"] / 2 if o["m0"] != 0 else 0
o1 = o["sq1"] / o["m1"] / 2 if o["m1"] != 0 else 0

# 判定
if llr < lower_bound + o0:
    state = "rejected"   # H0 採択
elif llr > upper_bound - o1:
    state = "accepted"   # H1 採択
```

## 漸近的性質

### GSPRT と SPRT の関係

Li, Liu, and Ying (2014) により、GSPRT は以下の漸近的性質を持つことが証明されています：[^gsprt-paper]

1. **漸近的に SPRT と同等**: 誤り率 \\(\alpha, \beta \to 0\\) のとき、GSPRT の判定領域は標準 SPRT の判定領域に収束する
2. **一様な漸近的最適性**: 任意の真のパラメータ値に対して、期待サンプル数が漸近的に最小となる
3. **Elo 差の大きさに非依存**: 検定される Elo 差が小さい必要はない

### 実用的な意味

これらの性質により、GSPRT を標準 SPRT と同様に運用できます：

- 判定境界 \\(\ln(\beta/(1-\alpha))\\) と \\(\ln((1-\beta)/\alpha)\\) はそのまま適用可能
- \\(\alpha = \beta = 0.05\\) という標準的な設定で、理論保証が十分に機能する

## 正規化 Elo での GSPRT

nElo を使う場合、検定統計量は「期待値」ではなく「t 値」になります。[^nelo-practical]

### t 値に基づく MLE

\\[
t = \frac{\mu - 0.5}{\sigma}
\\]

この t 値に対する制約付き MLE は、より複雑な反復計算が必要です（固定小数点法）：

```python
def MLE_t_value(pdfhat, ref, s):
    """t 値 = (mu - ref) / sigma が s であるという制約のもとで MLE を計算する。"""
    N = len(pdfhat)
    pdf_MLE = uniform(pdfhat)  # 一様分布で初期化
    for _ in range(10):  # 固定小数点反復
        mu, var = stats(pdf_MLE)
        sigma = var ** 0.5
        pdf1 = [
            (ai - ref - s * sigma * (1 + ((mu - ai) / sigma) ** 2) / 2, pi)
            for ai, pi in pdfhat
        ]
        x = secular(pdf1)
        pdf_MLE = [(pdfhat[i][0], pdfhat[i][1] / (1 + x * pdf1[i][0]))
                   for i in range(N)]
    return pdf_MLE
```

### nElo での LLR 近似

\\[
\text{LLR} \approx \frac{N}{2} \ln\frac{1 + (\text{nt} - \text{nt}_0)^2}{1 + (\text{nt} - \text{nt}_1)^2}
\\]

ここで \\(\text{nt}\\) は観測された正規化 t 値、\\(\text{nt}_0, \text{nt}_1\\) は閾値の正規化 t 値です。

## 三項・五項の自動選択

Fishtest の実装では、入力データの形式によって自動的にモデルが選択されます：

| 入力 | カテゴリ数 | 値の範囲 | 適用場面 |
|:---|:---:|:---|:---|
| \\([L, D, W]\\) | 3 | 0, 0.5, 1.0 | 三項（各ゲーム独立） |
| \\([n_0, n_1, n_2, n_3, n_4]\\) | 5 | 0, 0.5, 1.0, 1.5, 2.0 | 五項（ペアゲーム） |

五項分布の場合、スコアは 0〜2.0 の範囲を取り、期待値は 0.5 ではなく 1.0 が互角に対応します。
MLE の計算式は同一で、カテゴリ数とスコアの範囲だけが異なります。

## 実装リファレンス

| ファイル | 関数 | 役割 |
|---------|------|------|
| Fishtest `stats/LLRcalc.py` | `MLE_expected()` | 期待値制約付き MLE |
| Fishtest `stats/LLRcalc.py` | `MLE_t_value()` | t 値制約付き MLE |
| Fishtest `stats/LLRcalc.py` | `secular()` | 世俗方程式ソルバー |
| Fishtest `stats/LLRcalc.py` | `LLR()` | 正確な GSPRT-LLR |
| Fishtest `stats/LLRcalc.py` | `LLR_alt2()` | 2 次近似 LLR |
| Fishtest `stats/LLRcalc.py` | `LLR_logistic()` | Logistic Elo での LLR |
| Fishtest `stats/LLRcalc.py` | `LLR_normalized()` | nElo での LLR |
| Fishtest `stats/stat_util.py` | `update_SPRT()` | GSPRT の逐次更新とオーバーシュート補正 |

## 参考文献

[^gsprt-paper]: Xiaoou Li, Jingchen Liu, and Zhiliang Ying (2014). "Generalized Sequential Probability Ratio Test for Separate Families of Hypotheses". *Sequential Analysis*, 33(4), pp. 539-563. [PDF](https://stat.columbia.edu/~jcliu/paper/GSPRT_SQA3.pdf)

[^mle-multinomial]: Michel Van den Bergh, ["MLE for Multinomial"](https://www.cantate.be/Fishtest/support_MLE_multinomial.pdf) — 多項分布の制約付き MLE の理論

[^gsprt-approx]: Michel Van den Bergh, ["GSPRT Approximation"](https://www.cantate.be/Fishtest/GSPRT_approximation.pdf) — GSPRT のブラウン運動近似

[^brownian-approx]: Michel Van den Bergh, ["Brownian Approximation"](https://www.cantate.be/Fishtest/brownian_approximation.pdf) — GSPRT からの Elo 推定

[^nelo-practical]: Michel Van den Bergh, ["Normalized Elo"](https://www.cantate.be/Fishtest/normalized_elo_practical.pdf) — 正規化 Elo の実用的な解説

[^siegmund]: David Siegmund (1985). *Sequential Analysis: Tests and Confidence Intervals*. Springer. Corollary 8.33 — オーバーシュート補正の理論的基盤

[^gsprt-practical]: Michel Van den Bergh, ["A Practical Introduction to the GSPRT"](https://cantate.be/Fishtest/GSPRT_approximation.pdf) — GSPRT の実用的な導入解説

[^sprta]: Michel Van den Bergh, ["The SPRT for a Brownian Motion"](https://cantate.be/Fishtest/sprta.pdf) — ブラウン運動としての SPRT の理論

[^fishtest-math]: [Statistical Methods and Algorithms in Fishtest](https://official-stockfish.github.io/docs/fishtest-wiki/Fishtest-Mathematics.html) — Fishtest の統計手法の包括的ドキュメント

## 次に読む

→ **[五項分布モデル](./pentanomial.md)**: ペアゲームを活用した高精度な分析手法を解説します。
