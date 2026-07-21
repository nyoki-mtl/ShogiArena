# BayesElo と引き分けモデル

> **前提知識**：[Elo レーティング](./index.md)（勝率とレーティング差の関係）

## このページの要点

- BayesElo は引き分け率を独立したパラメータ（drawelo）として扱う Elo の拡張
- 標準的な Logistic Elo が期待スコアのみを扱うのに対し、BayesElo は勝ち、引き分け、負けの **3 つの確率**を個別に表す
- 引き分け率の高い将棋やチェスのエンジンテストでは、BayesElo のほうが対局結果の構造をそのまま反映できる
- ShogiArena と Fishtest はいずれも Logistic Elo を標準として使用しつつ、BayesElo との変換を内部的にサポートしている

## Logistic Elo が捨てている情報

標準的な Logistic Elo では、期待スコア \\(p\\) と Elo 差 \\(\Delta R\\) の関係は次のとおりです。

\\[
p = \frac{1}{1 + 10^{-\Delta R / 400}}
\\]

ここで \\(p\\) は**スコア**（勝ち=1.0, 引き分け=0.5, 負け=0.0）の期待値です。
引き分けは「半分の勝ち」に潰され、引き分け率そのものはモデルに残りません。

この潰し方は、同じスコアでも 1 局あたりに得られる情報量が違うという事実を落とします。
引き分けの多い条件（将棋の長時間対局、チェスの上位エンジン同士）では、多くの対局が勝敗の情報を運ばないため、同じスコアに対する推定の分散が大きくなります。
引き分け率は持ち時間やエンジンの特性に応じて動くので、この差は条件を変えるたびに現れます。

## BayesElo モデル

BayesElo は、引き分けの確率を **drawelo** というパラメータで独立に持たせます。[^bayeselo-origin]

### 3 つの確率

BayesElo パラメータ \\((\text{elo}, \text{drawelo})\\) から、三項分布の確率を計算します。

\\[
P_{\text{win}} = \frac{1}{1 + 10^{(-\text{elo} + \text{drawelo})/400}}
\\]

\\[
P_{\text{loss}} = \frac{1}{1 + 10^{(\text{elo} + \text{drawelo})/400}}
\\]

\\[
P_{\text{draw}} = 1 - P_{\text{win}} - P_{\text{loss}}
\\]

### drawelo の読み方

drawelo は、上の 2 式で勝ちと負けの確率を押し下げる向きに働きます。

- **drawelo が大きい**：勝ちと負けの確率が圧縮され、引き分け率が高くなる
- **drawelo が小さい**：勝ちと負けの確率が広がり、引き分け率が低くなる
- **drawelo = 0**：引き分け確率が 0 になる。モデル上は取りうるが、実際の対局条件ではまず現れない

```text
drawelo = 0 の場合:
  P_win = 1/(1+10^(-elo/400))
  P_loss = 1/(1+10^(elo/400))
  P_draw = 0

drawelo = 327 の場合（チェスの典型値）:
  elo=0 → P_win ≈ 0.16, P_draw ≈ 0.67, P_loss ≈ 0.16
  elo=50 → P_win ≈ 0.20, P_draw ≈ 0.64, P_loss ≈ 0.13
```

### 数値例

| elo | drawelo | \\(P_{\text{win}}\\) | \\(P_{\text{draw}}\\) | \\(P_{\text{loss}}\\) | Logistic Elo 換算 |
|:---:|:---:|:---:|:---:|:---:|:---:|
| 0 | 327 | 16.4% | 67.1% | 16.4% | 0.0 |
| 10 | 327 | 17.1% | 67.1% | 15.8% | 3.5 |
| 50 | 327 | 21.3% | 65.2% | 13.5% | 17.0 |
| 0 | 200 | 24.0% | 52.0% | 24.0% | 0.0 |
| 50 | 200 | 29.4% | 50.3% | 20.3% | 17.0 |

## Logistic Elo と BayesElo の変換

### BayesElo → Logistic Elo

```python
def bayeselo_to_elo(belo: float, drawelo: float) -> float:
    """BayesElo をスコア（期待値）に変換し、Logistic Elo に換算する。"""
    P_win = 1.0 / (1.0 + 10.0 ** ((-belo + drawelo) / 400.0))
    P_loss = 1.0 / (1.0 + 10.0 ** ((belo + drawelo) / 400.0))
    P_draw = 1.0 - P_win - P_loss
    score = P_win + 0.5 * P_draw
    return -400.0 * math.log10(1.0 / score - 1.0)
```

### Logistic Elo → BayesElo

逆変換は、三項確率から BayesElo パラメータを復元します。

\\[
\text{elo} = 200 \cdot \log_{10}\left(\frac{P_{\text{win}}}{P_{\text{loss}}} \cdot \frac{1 - P_{\text{loss}}}{1 - P_{\text{win}}}\right)
\\]

\\[
\text{drawelo} = 200 \cdot \log_{10}\left(\frac{1 - P_{\text{loss}}}{P_{\text{loss}}} \cdot \frac{1 - P_{\text{win}}}{P_{\text{win}}}\right)
\\]

```python
def proba_to_bayeselo(P: list[float]) -> tuple[float, float]:
    """三項確率 [P_loss, P_draw, P_win] から BayesElo パラメータを復元する。"""
    elo = 200 * math.log10(
        P[2] / P[0] * (1 - P[0]) / (1 - P[2])
    )
    drawelo = 200 * math.log10(
        (1 - P[0]) / P[0] * (1 - P[2]) / P[2]
    )
    return elo, drawelo
```

### drawelo の推定

対局結果から drawelo を推定するには、三項頻度 \\([L, D, W]\\) を正規化して確率に変換し、上記の逆変換を適用します。

```python
def draw_elo_calc(R: list[int]) -> float:
    """三項頻度 [losses, draws, wins] から drawelo を推定する。"""
    N = sum(R)
    P = [p / N for p in R]
    _, drawelo = proba_to_bayeselo(P)
    return drawelo
```

## LOS（Likelihood of Superiority）

LOS は、あるエンジンが対戦相手よりも強い確率を表す指標です。
厳密な仮説検定ではありませんが、対局の途中でどれだけ確信が持てているかを一つの数値で見るのに使えます。

### 計算式

\\[
\text{LOS} = \Phi\left(\frac{W - L}{\sqrt{W + L}}\right)
\\]

ここで \\(W\\) は勝ち数、\\(L\\) は負け数、\\(\Phi\\) は標準正規分布の累積分布関数です。
式に現れるのは勝ち数と負け数だけで、**引き分けは LOS の計算に入りません**。

誤差関数を使えば同じ値を次のようにも書けます。

\\[
\text{LOS} = \frac{1}{2}\left[1 + \text{erf}\left(\frac{W - L}{\sqrt{2(W + L)}}\right)\right]
\\]

### スコアベースの LOS

期待スコア \\(\mu\\) と標準偏差 \\(\sigma\\) を使うと、引き分けを含めたスコアの分布から LOS を書けます。

\\[
\text{LOS} = \Phi\left(\frac{\mu - 0.5}{\sigma / \sqrt{n}}\right)
\\]

ここで \\(n\\) は対局数（またはペア数）です。

| LOS | 解釈 |
|:---:|:---|
| > 95% | 優位性がほぼ確実 |
| 75-95% | 優位の傾向あり |
| 50-75% | 不明確 |
| < 50% | 劣位の可能性 |

> **注**：LOS はあくまで目安です。統計的に厳密な判定には [SPRT](../sprt/index.md) を使用してください。

## Fishtest が BayesElo から離れた理由

Fishtest は当初、BayesElo をベースにした SPRT を使用していました。
その後、次の理由から Logistic Elo モデルに移行しています。

1. **パラメータが 1 つで済む**：Logistic Elo は Elo 差だけでモデルが決まる
2. **drawelo 推定のバイアス**：drawelo を「アウトオブサンプル」で推定すると、小さなバイアスが生じる
3. **正規化 Elo の登場**：引き分け率への依存を排除する正規化 Elo（[nElo](./nelo.md)）が開発された

現在の Fishtest は 3 つの Elo モデルをサポートしています。

| モデル | 特徴 | 使用状況 |
|:---|:---|:---|
| **Logistic Elo** | シンプル、スコアベース | 広く使用 |
| **BayesElo** | 引き分け率を明示モデル化 | レガシーサポート |
| **Normalized Elo** | 引き分け率に非依存 | Fishtest の標準 |

## 実装リファレンス

| ファイル | クラス/関数 | 役割 |
|---------|----------|------|
| `_core/contexts/game_session/application/elo_rating_service.py` | `EloRatingService` | Logistic Elo の計算 |
| Fishtest `stats/stat_util.py` | `bayeselo_to_proba()` | BayesElo → 三項確率 |
| Fishtest `stats/stat_util.py` | `proba_to_bayeselo()` | 三項確率 → BayesElo |

## 参考文献

[^bayeselo-origin]: Rémi Coulom (2005). [BayesElo Rating](https://www.remi-coulom.fr/Bayesian-Elo/)（BayesElo の原著者による解説）
- Fishtest Statistics: [stat_util.py](https://github.com/official-stockfish/fishtest/blob/master/server/fishtest/stats/stat_util.py)（Fishtest の統計計算実装）

## 次に読む

- **[正規化 Elo（nElo）](./nelo.md)**：引き分け率に依存しない強さの指標
