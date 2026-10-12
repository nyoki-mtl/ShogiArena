# 統計的検定とチューニング

ShogiArena が使う統計手法とパラメータ最適化アルゴリズムの解説です。
Elo で強さの差を表し、SPRT で検定し、SPSA でパラメータを最適化する流れを説明します。

## 想定読者

- 大学レベルの確率、統計の基礎知識がある
- 仮説検定（帰無仮説、対立仮説、有意水準）の概念は理解している
- 将棋エンジンの開発や評価に携わっている、または興味がある
- 最適化アルゴリズムの基本概念（勾配降下法など）を知っている

## 各章の内容

1. **[Elo レーティング](./elo/index.md)**：エンジンの強さを数値化する基礎理論
   - **[BayesElo と引き分けモデル](./elo/bayeselo.md)**：引き分け率を明示的にモデル化する Elo の拡張と LOS
   - **[正規化 Elo（nElo）](./elo/nelo.md)**：引き分け率に依存しない強さの指標
2. **[SPRT（逐次確率比検定）](./sprt/index.md)**：エンジン間の強さの差を統計的に判定
   - **[対数尤度比（LLR）](./sprt/llr.md)**：SPRT の中心となる検定統計量
   - **[GSPRT（一般化 SPRT）](./sprt/gsprt.md)**：MLE ベースの正確な LLR 計算とオーバーシュート補正
   - **[五項分布モデル](./sprt/pentanomial.md)**：ペアゲームによる高精度な分析
3. **[SPSA（同時摂動確率近似法）](./spsa/index.md)**：エンジンパラメータの自動最適化
   - **[勾配推定と摂動](./spsa/gradient.md)**：Rademacher 摂動による効率的な勾配推定
   - **[ゲインスケジュール](./spsa/gain-schedule.md)**：収束を制御する減衰系列の設計
   - **[ノイズと高次手法の限界](./spsa/noise-and-higher-order.md)**：二次手法が使えない理由と実践知
   - **[LTC 回帰テスト](./spsa/ltc-regression.md)**：実行中の LTC 検証と、それを既定で無効にしている理由
4. **[分散削減テクニック](./variance-reduction/index.md)**：少ない対局数で高精度な推定を得る手法
5. **[用語集](./glossary.md)**：専門用語の定義と関連ページ
6. **[テストフレームワーク概説](./testing-frameworks.md)**：OpenBench、Fishtest、Cutechess の解説と ShogiArena の位置付け

SPSA で得たパラメータの検証は、チューニング終了後の独立した SPRT ランで行います。
LTC 回帰テストも判定に SPRT を使えます。
SPRT を先に理解してから SPSA に進むことを推奨します。

## 他のエンジンテストフレームワークとの比較

ShogiArena の統計モジュールは、以下のフレームワークの知見を参考に設計されています。
各フレームワークの詳細は [テストフレームワーク概説](./testing-frameworks.md) を参照してください。

| フレームワーク | 対象 | SPRT | SPSA | 五項分布 |
|-------------|------|------|------|---------|
| **ShogiArena** | 将棋 | ✓ | ✓ | ✓ |
| [cutechess](https://github.com/cutechess/cutechess) | チェス | ✓ | ✗ | ✓ |
| [OpenBench](https://github.com/AndyGrant/OpenBench) | チェス | ✓ | ✓ | ✓ |
| [fastchess](https://github.com/Disservin/fastchess) | チェス | ✓ | ✗ | ✓ |

## 次に読む

→ **[Elo レーティング](./elo/index.md)**：エンジンの強さを数値化する基礎理論
