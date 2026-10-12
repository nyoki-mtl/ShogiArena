# ShogiArena ドキュメント

ShogiArenaは、USI対応の将棋エンジンを自動で対局させ、結果を比較するPython製のツールです。
トーナメント、強さの検定（SPRT）、パラメータ調整（SPSA）、自己対局による棋譜生成に使えます。
対局の進行や保存した棋譜は、ブラウザのダッシュボードで確認できます。

## 初めて使う

[インストール](getting-started/installation.md)を済ませたら、[クイックスタート](getting-started/quick-start.md)で手元のエンジンを対局させてみてください。
エンジンをまだ用意していない場合は、クイックスタートにYaneuraOuと水匠5を取得する手順もあります。

用途別の設定例は[GitHubのexamples/configs](https://github.com/nyoki-mtl/ShogiArena/tree/main/examples/configs)にあります。

## 目的から探す

| やりたいこと | 読むページ |
| --- | --- |
| エンジンや対局条件を設定する | [エンジン設定](user-guide/engine-configuration.md)、[トーナメント](user-guide/tournaments.md) |
| 変更前後の強さを比べる、パラメータを調整する | [SPRT / SPSA](user-guide/spsa.md) |
| 対局や棋譜をブラウザで見る | [ダッシュボード](user-guide/dashboard.md) |
| 複数のマシンで対局を回す | [リモート実行](user-guide/remote-execution.md) |
| Pythonからエンジンを操作する | [Pythonライブラリ](user-guide/python-library.md) |
| コマンドの引数を調べる | [CLIリファレンス](api/cli.md) |
| レーティングや検定の仕組みを知る | [統計的検定とチューニング](internals/index.md) |

開発に参加する場合は、[コントリビュートガイド](development/contributing.md)を参照してください。
