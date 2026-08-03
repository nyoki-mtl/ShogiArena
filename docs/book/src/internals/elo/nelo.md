# 正規化 Elo（nElo）

> **前提知識**：[Elo レーティング](./index.md)、[BayesElo](./bayeselo.md)

## このページの要点

- 正規化 Elo（nElo）は、スコアの標準偏差で正規化した Elo 差
- 引き分け率や開局集（定跡ファイル）が変わっても値の動きが小さく、**エンジンの強さの差**に近い量として扱える
- Fishtest では検定の閾値（elo0, elo1）を nElo で表現し、テストの所要対局数を安定化させている
- 所要対局数は nElo の 2 乗に反比例する近似式で見積もれる

## 引き分け率で動いてしまう Elo 差

通常の Logistic Elo は、同じエンジンペアでも引き分け率によって値が変わります。

```text
引き分け率 30%（STC）: 勝率 55% → Elo差 ≈ 35
引き分け率 60%（LTC）: 勝率 52.5% → Elo差 ≈ 17

同じエンジンペアでも、持ち時間（＝引き分け率）で Elo 差が変わる
```

Elo 差が対局条件ごとに動くと、次の不都合が生じます。

- STC と LTC の結果を直接比較できない
- テストの閾値設定が持ち時間に依存してしまう
- 開局集（定跡ファイル）の偏りが結果を歪める

正規化 Elo は、スコアの散らばりを尺度に取り込むことで、この条件依存を取り除きます。

## 定義

### スコアの統計量

対局結果（勝ち=1.0, 引き分け=0.5, 負け=0.0）から、期待スコア \\(\mu\\) と分散 \\(\sigma^2\\) を計算します。

**三項分布（個別対局）の場合**：

\\[
\mu = \frac{W + 0.5 D}{N}, \quad \sigma^2 = \frac{W + 0.25 D}{N} - \mu^2
\\]

ここで \\(N = W + D + L\\)（総対局数）。

**五項分布（ペアゲーム）の場合**：

\\[
\mu = \frac{\sum_{k=0}^{4} \frac{k}{4} \cdot n_k}{N_{\text{pairs}}}, \quad \sigma_{\text{pg}}^2 = 2 \cdot \text{Var}[\text{per-pair score}]
\\]

ここで \\(n_k\\) はペアスコア \\(k/2\\) のカウントです。
式中の \\(\mu\\) は 1 局単位のスコア（0〜1）なので、ペアスコアを 2 で割った \\(k/4\\) を使います。
ペアゲームスコアの標準偏差は \\(\sigma_{\text{pg}} = \sqrt{2 \cdot \text{Var}}\\) です。

### 正規化 Elo の定義

正規化された t 値（normalized t-value）を次のように定義します。

\\[
\text{nt} = \frac{\mu - 0.5}{\sigma_{\text{pg}}}
\\]

ここで \\(\sigma_{\text{pg}}\\) は**ペアゲームスコア**の標準偏差です。
三項分布の場合は \\(\sigma_{\text{pg}} = \sigma\\)（1 局のスコアの標準偏差）、五項分布の場合は \\(\sigma_{\text{pg}} = \sqrt{2 \cdot \text{Var}\_{\text{pair}}}\\) です。

正規化 Elo は、この t 値にスケーリング定数を掛けたものです。

\\[
\text{nElo} = \frac{800}{\ln 10} \cdot \text{nt} \approx 347.44 \cdot \text{nt}
\\]

スケーリング定数 \\(800 / \ln 10 \approx 347.44\\) は、**引き分け率ゼロ、完全にバランスされた開局集**のとき、nElo と Logistic Elo が一致するように選ばれています。

### 等価な表現

\\[
\text{nElo} = \frac{800}{\ln 10} \cdot \frac{\mu - 0.5}{\sigma_{\text{pg}}}
\\]

## 引き分け率への非依存性

正規化 Elo は、**引き分け率が変化しても値がほぼ一定**に保たれます。

### 分子と分母が同時に縮む

引き分け率が上がると、次の二つが同時に起こります。

- スコア \\(\mu\\) が 0.5 に近づき、分子 \\(\mu - 0.5\\) が小さくなる
- 勝敗の割合が減ってスコアの散らばりも狭まり、分母の標準偏差 \\(\sigma\\) が小さくなる

分子と分母が同じ向きに縮むため、比 \\((\mu - 0.5) / \sigma\\) の変化は打ち消し合います。

### 数値例

| 引き分け率 | \\(\mu\\) | \\(\sigma\\) | Logistic Elo | nElo |
|:---:|:---:|:---:|:---:|:---:|
| 0% | 0.570 | 0.495 | 49.2 | 49.1 |
| 30% | 0.549 | 0.426 | 34.2 | 39.9 |
| 50% | 0.535 | 0.370 | 24.4 | 32.8 |
| 70% | 0.521 | 0.295 | 14.6 | 24.7 |

Logistic Elo は引き分け率によって 14.6 から 49.2 まで 3 倍以上に振れるのに対し、nElo の振れ幅は 2 倍程度に収まっています。

> **注**：完全な非依存性が理論的に保証されるわけではなく、あくまで近似です。実用上は、条件をまたいだ比較に耐える程度には安定しています。

## テスト閾値としての nElo

Fishtest では、SPRT の閾値（elo0, elo1）を nElo で表現しています。

### 所要対局数が条件に左右されなくなる

SPRT が必要とする対局数は、閾値をスコアの散らばりで測ったときの大きさで決まります。
nElo は最初から標準偏差で割った量なので、nElo で閾値を設定すると、**所要対局数が引き分け率や開局集にほとんど左右されなくなります**。

```text
Logistic Elo で閾値設定:
  elo0=0, elo1=5（STC, 引き分け率30%）→ 約 20,000 対局
  elo0=0, elo1=5（LTC, 引き分け率60%）→ 約 60,000 対局
  → 同じ閾値なのに所要対局数が 3 倍も違う！

nElo で閾値設定:
  nelo0=0, nelo1=5（STC, 引き分け率30%）→ 約 25,000 対局
  nelo0=0, nelo1=5（LTC, 引き分け率60%）→ 約 25,000 対局
  → 所要対局数がほぼ一定
```

### 所要対局数の近似式

SPRT テストの所要対局数（ペアゲーム数）は、nElo の 2 乗に反比例します。

\\[
N_{\text{pairs}} \approx \frac{640{,}000}{(\text{nElo})^2}
\\]

この近似は \\(\alpha = \beta = 0.05\\) の場合に成り立ちます。

| nElo | 概算所要ペア数 | 概算所要対局数 |
|:---:|:---:|:---:|
| 1 | 640,000 | 1,280,000 |
| 3 | 71,111 | 142,222 |
| 5 | 25,600 | 51,200 |
| 10 | 6,400 | 12,800 |

## Logistic Elo から nElo への変換

Logistic Elo だけでは分母の標準偏差が決まらないため、変換には引き分け率の情報が要ります。

### 引き分け率を与えて変換する

```python
def logistic_elo_to_nelo(elo: float, draw_ratio: float) -> float:
    """Logistic Elo と引き分け率から nElo を計算する。"""
    s = 1.0 / (1.0 + 10.0 ** (-elo / 400.0))  # スコア
    # 三項確率の復元
    P_win = s - draw_ratio / 2.0
    P_draw = draw_ratio
    P_loss = 1.0 - P_win - P_draw
    # スコアの分散
    var = P_win + 0.25 * P_draw - s * s
    sigma = math.sqrt(var)
    # nElo
    nt = (s - 0.5) / sigma
    return 800.0 / math.log(10) * nt
```

### 観測された頻度から直接計算する

```python
def nelo_from_results(results: list[int]) -> float:
    """三項頻度 [L, D, W] または五項頻度 [LL, LD, DD/WL, WD, WW] から nElo を計算する。"""
    count = len(results)
    N = sum(results)
    games = N * (count - 1) / 2.0
    mu = sum(results[i] * (i / 2.0) for i in range(count)) / games
    mu_ = (count - 1) / 2.0 * mu
    var = sum(results[i] * (i / 2.0 - mu_) ** 2.0 for i in range(count)) / games

    if count == 5:
        sigma_pg = (2 * var) ** 0.5
    else:
        sigma_pg = var ** 0.5

    nt = (mu - 0.5) / sigma_pg
    return 800.0 / math.log(10) * nt
```

## 正規化 Elo ベースの LLR 計算

nElo を使って LLR を計算する場合、検定統計量には期待値ではなく **t 値**（\\((\mu - 0.5) / \sigma\\)）を使用します。

### 近似公式

\\[
\text{LLR} \approx \frac{N}{2} \ln\left(\frac{1 + (\text{nt} - \text{nt}\_0)^2}{1 + (\text{nt} - \text{nt}\_1)^2}\right)
\\]

ここで \\(\text{nt}\_0, \text{nt}\_1\\) は閾値を正規化 t 値に変換したもの、\\(\text{nt}\\) は観測された正規化 t 値です。

### 正確な計算

正確な LLR は、t 値に関する MLE（最尤推定）を用いた GSPRT として計算されます。
詳細は [GSPRT（一般化逐次確率比検定）](../sprt/gsprt.md) を参照してください。

## ShogiArena での位置付け

ShogiArena のレーティング計算は Logistic Elo をベースにしていますが、nElo は次の場面で使えます。

- STC と LTC の結果を比較する際の共通スケール
- テスト閾値の設計と所要対局数の見積もり
- ダッシュボードでの結果表示（nElo を併記すると、持ち時間の違いによる見かけの差が消える）

## 参考文献

- Michel Van den Bergh, ["Normalized Elo"](https://www.cantate.be/Fishtest/normalized_elo_practical.pdf)（正規化 Elo の理論的基盤と実用的な計算法）
- Fishtest Statistics: [LLRcalc.py](https://github.com/official-stockfish/fishtest/blob/master/server/fishtest/stats/LLRcalc.py)（`LLR_normalized()` の実装）

## 次に読む

- **[SPRT（逐次確率比検定）](../sprt/index.md)**：Elo 差の統計的有意性を判定する手法
