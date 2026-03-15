# SPRT（逐次確率比検定）

> **前提知識**: [Elo レーティング](../elo/index.md)（勝率と Elo 差の関係）

## このページの要点

- SPRT は「対局を 1 局ずつ追加しながら判定する」逐次検定。固定サンプルサイズの検定より効率的
- 2 つの仮説（H0: Elo 差 ≤ elo0、H1: Elo 差 ≥ elo1）を対数尤度比（LLR）で比較する
- LLR が上限に達すれば H1 を採択（改善あり）、下限に達すれば H0 を採択（改善なし）
- α（偽陽性率）と β（偽陰性率）で検定の厳しさを制御する。デフォルトはどちらも 0.05

## なぜ SPRT が必要か

エンジン A とエンジン B を 1000 局対局させて、A の勝率が 52% だったとします。
これは「A が B より強い」と結論づけてよいでしょうか？

**そうとは限りません。** コイン投げ（勝率 50%）でも 1000 回中 520 回表が出る確率は十分にあります。

従来の固定サンプルサイズ検定では、事前に「何局対局するか」を決め、全対局が終わった後に判定します。
しかしエンジンテストでは以下の問題があります。

- **対局は高コスト**: 1 局に数秒〜数分かかり、計算資源を消費する
- **差が大きいときは少ない局数で判定できるはず**: 勝率 70% なら 100 局で十分明らか
- **差が小さいときはもっと多くの局数が必要**: 勝率 51% なら数万局必要かもしれない
- **途中で結果を見てしまう問題**（覗き見問題）: 固定サンプルサイズ検定では、途中結果を見て「有意水準を満たしたから止めよう」と判断すると、統計的妥当性が失われる。サンプル数を恣意的に変更することは、実質的に多重検定を行っていることと等価であり、偽陽性率が設定値を超えてしまう

SPRT はこの問題を解決します。**対局を 1 局ずつ追加しながら、十分な証拠が集まった時点で判定を下す**のです。

## 仮説の設定

SPRT では 2 つの仮説を明示的に定義します。

\\[
H_0: \Delta\text{Elo} \leq \text{elo0} \quad \text{（改善なし、または劣化）}
\\]

\\[
H_1: \Delta\text{Elo} \geq \text{elo1} \quad \text{（十分な改善あり）}
\\]

### パラメータの意味

| パラメータ | 意味 | 典型値 |
|:---:|:---|:---:|
| `elo0` | H0 の Elo 差。これ以下なら「改善なし」と判定 | 0 |
| `elo1` | H1 の Elo 差。これ以上なら「改善あり」と判定 | 5 |
| `α` | 第一種の過誤率（偽陽性率）。H0 が真なのに H1 を採択する確率 | 0.05 |
| `β` | 第二種の過誤率（偽陰性率）。H1 が真なのに H0 を採択する確率 | 0.05 |

### elo0 と elo1 の選び方

`elo0` と `elo1` の間隔は「無関心領域（indifference zone）」と呼ばれ、この領域内の Elo 差については判定を保留します。

```text
← H0 採択 →  ← 無関心領域 →  ← H1 採択 →
──────────────┬──────────────┬──────────────→ Elo 差
            elo0           elo1
```

- **elo0 = 0, elo1 = 5**: 「5 Elo 以上の改善があるか？」を検定（一般的な設定）
- **elo0 = -5, elo1 = 5**: 「改善も劣化もないか？」を双方向に検定
- **elo0 = 0, elo1 = 3**: より小さな改善を検出（ただし必要対局数が増加）

間隔を狭くするほど検出感度は上がりますが、判定に必要なゲーム数が急増します。

#### 変更の種類に応じた設定

Fishtest（Stockfish のテストフレームワーク）では、変更の種類に応じて elo0/elo1 を使い分けます。

| 変更の種類 | elo0/elo1 の例 | 目的 |
|:---|:---:|:---|
| **コード削除・リファクタリング** | [-1.75, 0.25] | 性能劣化がないことの確認（non-regression） |
| **アルゴリズム改善** | [0, 2] や [0.5, 2.5] | 改善効果の実証 |
| **一般的な変更** | [0, 5] | 標準的な改善検出 |

コード削除のように「改善がなくても、劣化していなければ良い」場合は、elo0 を負の値に設定します。
一方、新しいアルゴリズムのように「明確な改善があるはず」の場合は、elo0 を 0 以上にして
改善効果の実証を要求します。

また、Fishtest では STC（短時間制御）と LTC（長時間制御）で異なる設定を使用することがあります。
STC では広めの無関心領域（例: [0, 5]）で高速にスクリーニングし、LTC では狭い領域（例: [0, 2.5]）でより厳密に検証するという階層的なアプローチが一般的です。

> **参考**: [Chess Programming Wiki](https://www.chessprogramming.org/Sequential_Probability_Ratio_Test) にも、一般的に使用される elo0/elo1 の設定が記載されています。

## 判定の仕組み

### 境界値の計算

誤り率 α と β から、LLR の判定境界を計算します。

\\[
\text{lower} = \ln\frac{\beta}{1 - \alpha} \qquad \text{upper} = \ln\frac{1 - \beta}{\alpha}
\\]

α = β = 0.05 の場合：

\\[
\text{lower} = \ln\frac{0.05}{0.95} \approx -2.944 \qquad \text{upper} = \ln\frac{0.95}{0.05} \approx +2.944
\\]

```python
class Sprt:
    def __init__(self, elo0: float, elo1: float,
                 alpha: float = 0.05, beta: float = 0.05):
        self.lower_bound = math.log(beta / (1 - alpha))
        self.upper_bound = math.log((1 - beta) / alpha)
```

### 判定ルール

対局ごとに対数尤度比（LLR）を更新し、境界と比較します。

```text
LLR ≥ upper  →  H1 を採択（改善あり）
LLR ≤ lower  →  H0 を採択（改善なし）
lower < LLR < upper  →  判定保留、対局を続行
```

```python
def _make_decision(self) -> SprtDecision:
    if self.llr >= self.upper_bound:
        return SprtDecision.ACCEPT_H1
    elif self.llr <= self.lower_bound:
        return SprtDecision.ACCEPT_H0
    else:
        return SprtDecision.CONTINUE
```

## SPRT の動きを視覚的に理解する

LLR の推移を時系列で見ると、SPRT の判定プロセスがよくわかります。

```text
LLR
 +2.94 ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─  上限 (H1 採択)
       │                              ╱
       │                           ╱ ╱
       │                        ╱╱╱
       │                     ╱╱╱
  0.00 ┼─────────────╱╱╱──────────────────────────
       │          ╱╱╱
       │       ╱╱
       │     ╱
       │   ╱
 -2.94 ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─  下限 (H0 採択)
       └──────────────────────────────────────→ 対局数
```

- LLR が上限に先に到達すれば **H1 採択**（改善が確認された）
- LLR が下限に先に到達すれば **H0 採択**（改善は確認されなかった）
- 実際の Elo 差が大きいほど、少ない対局数で判定に到達する

## 誤り率の意味と影響

### α と β のトレードオフ

| 設定 | 偽陽性率 | 偽陰性率 | 所要ゲーム数 | 用途 |
|:---:|:---:|:---:|:---:|:---|
| α=0.05, β=0.05 | 5% | 5% | 標準 | 一般的な検定 |
| α=0.01, β=0.01 | 1% | 1% | 多い | 厳密な判定 |
| α=0.10, β=0.10 | 10% | 10% | 少ない | 予備的なスクリーニング |

α と β を小さくすると検定は厳しくなりますが、判定に必要なゲーム数が増加します。
エンジン開発の実践では α = β = 0.05 が標準的です。

### 具体例：必要ゲーム数の目安

elo0=0, elo1=5, α=β=0.05 の設定で、真の Elo 差が異なる場合の概算必要ゲーム数：

| 真の Elo 差 | 概算必要ゲーム数 | 期待される判定 |
|:---:|:---:|:---|
| 0 | ~20,000 | H0 採択 |
| 3 | ~40,000 | どちらもあり得る |
| 5 | ~20,000 | H1 採択 |
| 10 | ~5,000 | H1 採択 |
| 30 | ~500 | H1 採択 |

必要ゲーム数は、実力差の大きさに加えて引き分け率や定跡の影響によっても変動します。
実力差が小さい場合は数千〜数万局が必要になるため、最大ゲーム数は 10 万〜数十万に設定するのが一般的です。

> **ツール**: Fishtest が提供する [SPRT Calculator](https://tests.stockfishchess.org/sprt_calc) で、各種パラメータに対する必要ゲーム数を事前に計算できます。

## 設定例

```yaml
sprt:
  elo0: 0.0       # H0: 改善なし
  elo1: 5.0       # H1: 5 Elo 以上の改善
  alpha: 0.05     # 偽陽性率 5%
  beta: 0.05      # 偽陰性率 5%
  min_games: 1000  # 最低対局数（早すぎる判定を防ぐ）
  max_games: 0     # 上限なし（判定に到達するまで続行）
```

`min_games` を設定することで、対局数が少なすぎる段階での不安定な判定を防ぐことができます。

## 実装リファレンス

| ファイル | クラス/関数 | 役割 |
|---------|----------|------|
| `_core/contexts/game_session/application/sprt_service.py` | `Sprt` | SPRT 実装本体 |
| `_core/contexts/game_session/application/sprt_service.py` | `SprtDecision` | 判定結果の列挙型 |
| `_core/contexts/game_session/application/sprt_service.py` | `SprtResult` | 結果データクラス |
| `_core/contexts/game_session/adapters/orchestration/config_core.py` | `SprtConfig` | 設定スキーマ |

## SPRT と GSPRT

本ページで説明した SPRT は、仮説のもとでの確率分布が完全に特定されている（単純仮説）場合の理論です。
実際のエンジンテストでは、引き分け率などの未知パラメータが存在するため、
仮説を完全には特定できません。

Fishtest をはじめとする本格的な実装では、**GSPRT（一般化逐次確率比検定）** を使用しています。
GSPRT は、未知パラメータを最尤推定量（MLE）で置き換えることで、複合仮説に対応します。

詳細は [GSPRT（一般化逐次確率比検定）](./gsprt.md) を参照してください。

## 読了順序

- **[対数尤度比（LLR）](./llr.md)** — SPRT の核心となる検定統計量の詳細な計算方法
- **[GSPRT（一般化逐次確率比検定）](./gsprt.md)** — MLE ベースの正確な LLR 計算とオーバーシュート補正
- **[五項分布モデル](./pentanomial.md)** — ペアゲームを活用した高精度な分析手法

## 参考文献

- Abraham Wald (1945). "Sequential Tests of Statistical Hypotheses". *Annals of Mathematical Statistics* 16(2), pp. 117-186.
- [Stockfish Testing Framework (Fishtest)](https://tests.stockfishchess.org/)
- Xiaoou Li, Jingchen Liu, and Zhiliang Ying (2014). "Generalized Sequential Probability Ratio Test for Separate Families of Hypotheses". *Sequential Analysis*, 33(4), pp. 539-563.
- [Chess Programming Wiki: Sequential Probability Ratio Test](https://www.chessprogramming.org/Sequential_Probability_Ratio_Test)
- [Chess Programming Wiki: Match Statistics](https://www.chessprogramming.org/Match_Statistics#SPRT)
- [Statistical Methods and Algorithms in Fishtest](https://official-stockfish.github.io/docs/fishtest-wiki/Fishtest-Mathematics.html)

## 次に読む

→ **[対数尤度比（LLR）](./llr.md)**: SPRT の判定を支える統計量の計算方法を詳しく見ていきます。
