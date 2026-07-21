# 対数尤度比（LLR）

> **前提知識**：[SPRT（逐次確率比検定）](./index.md)（仮説、境界値、判定ルール）

## このページの要点

- LLR（Log-Likelihood Ratio）は、観測データに対する「H1 の尤度」と「H0 の尤度」の比の対数
- 各対局結果に対して尤度比を計算し、その対数を累積していく
- 勝ちと負けでは尤度比が異なり、ShogiArena の現在の実装では引き分けの尤度比を 1.0 とする
- LLR は加法的であるため、逐次的に更新でき、SPRT の逐次判定を可能にする
- 実用的には [GSPRT](./gsprt.md) による MLE ベースの計算が正確であり、その近似公式もこのページで扱う

## 尤度比の考え方

2 つの仮説を考えます。

- **H0**：エンジン A の勝率は \\(p_0\\)（Elo 差 = elo0 から算出）
- **H1**：エンジン A の勝率は \\(p_1\\)（Elo 差 = elo1 から算出）

対局で A が勝った場合、H1 のもとで勝ちが起きる確率は \\(p_1\\)、H0 のもとでは \\(p_0\\) です。

\\[
\text{尤度比} = \frac{p_1}{p_0}
\\]

もし \\(p_1 > p_0\\) なら尤度比は 1 より大きく、この勝ちは H1 を支持する証拠です。

## Elo 差から勝率への変換

仮説の Elo 差から期待勝率を算出します。

\\[
p_0 = \frac{1}{1 + 10^{-\text{elo0}/400}} \qquad p_1 = \frac{1}{1 + 10^{-\text{elo1}/400}}
\\]

**例**：elo0 = 0, elo1 = 5 の場合

\\[
p_0 = \frac{1}{1 + 10^{0}} = 0.5000 \qquad p_1 = \frac{1}{1 + 10^{-5/400}} = 0.5072
\\]

## 結果ごとの尤度比

各対局結果に対する尤度比を以下のように計算します。

### 勝ちの場合

\\[
\text{LR}_{\text{win}} = \frac{p_1}{p_0}
\\]

### 負けの場合

\\[
\text{LR}_{\text{loss}} = \frac{1 - p_1}{1 - p_0}
\\]

### 引き分けの場合

ShogiArena の現在の実装では、引き分けの尤度比を 1.0（= 中立的証拠）として扱っています。

\\[
\text{LR}_{\text{draw}} = 1.0
\\]

> **注**：より精密な実装では、引き分け率をモデルに組み込む BayesElo アプローチや
> [五項分布モデル](./pentanomial.md) を使用します。

## LLR の累積更新

LLR は各対局の尤度比の対数を累積します。

\\[
\text{LLR}_n = \sum_{i=1}^{n} \ln(\text{LR}_i) = \text{LLR}_{n-1} + \ln(\text{LR}_n)
\\]

対数の性質（積 → 和への変換）により、LLR は加法的に更新でき、
新しい対局が追加されるたびにインクリメンタルに計算できます。

```python
def _update_llr(self, result: GameResult) -> None:
    p0 = 1.0 / (1.0 + 10 ** (-self.elo0 / 400))
    p1 = 1.0 / (1.0 + 10 ** (-self.elo1 / 400))

    if result == WHITE_WIN:
        lr = p1 / p0
    elif result == BLACK_WIN:
        lr = (1 - p1) / (1 - p0)
    elif result.is_draw():
        lr = 1.0
    else:
        return

    self.llr += math.log(lr)
```

## 数値例

elo0=0, elo1=5（\\(p_0 = 0.5\\), \\(p_1 = 0.5072\\)）で 10 局の対局結果を追跡します。

| 局 | 結果 | LR | ln(LR) | 累積 LLR | 判定 |
|:---:|:---:|:---:|:---:|:---:|:---|
| 1 | 勝 | 1.0144 | +0.0143 | +0.014 | 続行 |
| 2 | 勝 | 1.0144 | +0.0143 | +0.029 | 続行 |
| 3 | 負 | 0.9854 | -0.0147 | +0.014 | 続行 |
| 4 | 勝 | 1.0144 | +0.0143 | +0.028 | 続行 |
| 5 | 引 | 1.0000 | 0.0000 | +0.028 | 続行 |
| 6 | 勝 | 1.0144 | +0.0143 | +0.043 | 続行 |
| 7 | 負 | 0.9854 | -0.0147 | +0.028 | 続行 |
| 8 | 勝 | 1.0144 | +0.0143 | +0.042 | 続行 |
| 9 | 勝 | 1.0144 | +0.0143 | +0.057 | 続行 |
| 10 | 勝 | 1.0144 | +0.0143 | +0.071 | 続行 |

LLR は +0.071 で、上限（+2.944）にも下限（-2.944）にも達していません。
elo0=0, elo1=5 のような小さな差を検定する場合、判定には数千〜数万局が必要です。

## GSPRT ベースの LLR 計算

ここまでの「1 局ごとの尤度比を累積する」方式は単純ですが、引き分けが LLR にまったく寄与しません。
Fishtest をはじめとする実装では、[GSPRT（一般化逐次確率比検定）](./gsprt.md) に基づく LLR 計算を使用します。

### 2 次近似公式

GSPRT-LLR には、多くの実装で標準的に使われる次の近似式があります。

\\[
\text{LLR} \approx \frac{N \cdot (s_1 - s_0)(2\hat{s} - s_0 - s_1)}{2 \hat{\sigma}^2}
\\]

記号の意味は次のとおりです。

| 記号 | 意味 |
|:---:|:---|
| \\(N\\) | サンプル数（対局数またはペア数） |
| \\(\hat{s}\\) | 観測されたスコアの期待値 |
| \\(s_0\\) | H0 のもとでの期待スコア = \\(1/(1+10^{-\text{elo0}/400})\\) |
| \\(s_1\\) | H1 のもとでの期待スコア = \\(1/(1+10^{-\text{elo1}/400})\\) |
| \\(\hat{\sigma}^2\\) | 観測されたスコアの分散 |

```python
def LLR_approx(elo0: float, elo1: float, results: list[int]) -> float:
    """三項/五項頻度から近似 GSPRT-LLR を計算する。"""
    s0, s1 = [1.0 / (1 + 10 ** (-elo / 400.0)) for elo in (elo0, elo1)]
    count = len(results)
    N = sum(results)
    games = N * (count - 1) / 2.0
    mu = sum(results[i] * (i / 2.0) for i in range(count)) / games
    mu_ = (count - 1) / 2.0 * mu
    var = sum(results[i] * (i / 2.0 - mu_) ** 2.0 for i in range(count)) / games
    return games * (s1 - s0) * (2 * mu - s0 - s1) / (2.0 * var)
```

### 正確な GSPRT-LLR

正確な計算では、制約付き最尤推定量（MLE）を使用します。
詳細は [GSPRT](./gsprt.md) ページを参照してください。

\\[
\text{LLR} = N \sum_{i} \hat{p}_i \cdot \ln\frac{p_{1,i}^{\text{MLE}}}{p_{0,i}^{\text{MLE}}}
\\]

### Elo 推定と信頼区間

SPRT テストの途中でも完了後でも、その時点のデータから Elo 差を推定し、95% 信頼区間を計算できます。

\\[
\hat{\text{Elo}} = -400 \cdot \log_{10}\left(\frac{1}{\hat{s}} - 1\right)
\\]

\\[
\text{Elo}_{95} = \frac{\text{Elo}(\hat{s} + 1.96 \cdot \hat{\sigma}/\sqrt{N}) - \text{Elo}(\hat{s} - 1.96 \cdot \hat{\sigma}/\sqrt{N})}{2}
\\]

```python
def get_elo(results: list[int]) -> tuple[float, float, float]:
    """三項/五項頻度から Elo 推定、95%信頼区間、LOS を計算する。"""
    games, mu, var = stats(results)
    stdev = math.sqrt(var)
    mu_min = mu + Phi_inv(0.025) * stdev / math.sqrt(games)
    mu_max = mu + Phi_inv(0.975) * stdev / math.sqrt(games)
    el = elo(mu)
    elo95 = (elo(mu_max) - elo(mu_min)) / 2.0
    los = Phi((mu - 0.5) / (stdev / math.sqrt(games)))
    return el, elo95, los
```

## LLR の性質

### 加法性

\\[
\text{LLR}_{a+b} = \text{LLR}_a + \text{LLR}_b
\\]

対局を分割して計算しても結果は変わらないため、検定の中断と再開が容易であり、並列計算とも相性がよいです。

### 単調性の非保証

LLR は単調に増加（または減少）するとは限りません。
勝ちと負けで符号が異なるため、ランダムウォークのように上下しながら境界に到達します。

### 引き分けの影響

現在の実装（LR=1.0）では、引き分けは LLR に影響を与えません。
そのため引き分け率が高いほど、1 局あたりに得られる情報が減り、判定に到達するまでの対局数が増加します。
引き分けを含めてモデル化する手法として [五項分布モデル](./pentanomial.md) があります。

## スナップショットと永続化

SPRT の状態は任意の時点で保存と復元ができます。
そのため、長期にわたる検定をプロセスの再起動を跨いで継続できます。

```python
@dataclass
class SprtResult:
    llr: float                  # 現在の LLR 値
    lower_bound: float          # H0 採択境界
    upper_bound: float          # H1 採択境界
    decision: SprtDecision      # CONTINUE / ACCEPT_H0 / ACCEPT_H1
    games_played: int           # 対局数
    wins: int
    draws: int
    losses: int
    win_rate: float             # (wins + 0.5 * draws) / games_played
    elo_estimate: float | None  # 推定 Elo 差
```

## 実装リファレンス

| ファイル | 関数 | 役割 |
|---------|------|------|
| `_core/contexts/game_session/application/sprt_service.py` | `_update_llr()` | 尤度比の計算と LLR の更新 |
| `_core/contexts/game_session/application/sprt_service.py` | `to_snapshot()` | 状態の永続化 |
| `_core/contexts/game_session/application/sprt_service.py` | `from_snapshot()` | 状態の復元 |
| Fishtest `stats/LLRcalc.py` | `LLR()` | GSPRT ベースの正確な LLR |
| Fishtest `stats/LLRcalc.py` | `LLR_alt2()` | 2 次近似 LLR |
| Fishtest `stats/stat_util.py` | `get_elo()` | Elo 推定と信頼区間 |

## 参考文献

- Michel Van den Bergh, ["MLE for Multinomial"](https://www.cantate.be/Fishtest/support_MLE_multinomial.pdf) — LLR 計算に使われる制約付き MLE の理論
- Michel Van den Bergh, ["GSPRT Approximation"](https://www.cantate.be/Fishtest/GSPRT_approximation.pdf) — 2 次近似の導出

## 次に読む

- **[GSPRT（一般化逐次確率比検定）](./gsprt.md)**：MLE ベースの LLR 計算とオーバーシュート補正
- **[五項分布モデル](./pentanomial.md)**：ペアゲームを単位とする分析手法
