# プロジェクト構成

Pythonパッケージは`src/shogiarena/`にあります。
エンジン操作やトーナメントの入口となるモジュールと、機能ごとの実装に分かれています。

## Pythonパッケージ

```text
src/shogiarena/
├── cli.py
├── composition.py
├── engine.py
├── tournament.py
└── _core/
    ├── contexts/
    ├── platform/
    ├── interfaces/
    └── shared/kernel/
```

| モジュール | 役割 |
| --- | --- |
| [engine.py](https://github.com/nyoki-mtl/ShogiArena/blob/main/src/shogiarena/engine.py) | USIエンジンの起動、探索、解析 |
| [tournament.py](https://github.com/nyoki-mtl/ShogiArena/blob/main/src/shogiarena/tournament.py) | トーナメントの設定と実行 |
| [cli.py](https://github.com/nyoki-mtl/ShogiArena/blob/main/src/shogiarena/cli.py) | コマンドラインの入口 |
| [composition.py](https://github.com/nyoki-mtl/ShogiArena/blob/main/src/shogiarena/composition.py) | 実行に必要なサービスの組み立て |

## 機能ごとの実装

| ディレクトリ | 内容 |
| --- | --- |
| [contexts](https://github.com/nyoki-mtl/ShogiArena/tree/main/src/shogiarena/_core/contexts) | 対局、トーナメント、SPSAなどの処理 |
| [platform](https://github.com/nyoki-mtl/ShogiArena/tree/main/src/shogiarena/_core/platform) | DB、ファイル、エンジンプロセス、ネットワークの操作 |
| [interfaces](https://github.com/nyoki-mtl/ShogiArena/tree/main/src/shogiarena/_core/interfaces) | CLI、ダッシュボード、設定の読み込み |
| [shared/kernel](https://github.com/nyoki-mtl/ShogiArena/tree/main/src/shogiarena/_core/shared/kernel) | 統計計算や持ち時間などの共通処理 |

`contexts`では、計算やルールを`domain`、処理の流れを`application`、外部への接続口を`ports`、その実装を`adapters`に置いています。
サービスを組み合わせる処理は[composition root](https://github.com/nyoki-mtl/ShogiArena/tree/main/src/shogiarena/_core/interfaces/composition_root)にあります。

## その他のディレクトリ

- [examples/configs](https://github.com/nyoki-mtl/ShogiArena/tree/main/examples/configs)：用途別の設定例
- [tests](https://github.com/nyoki-mtl/ShogiArena/tree/main/tests)：Pythonのテスト
- [ダッシュボードのフロントエンド](https://github.com/nyoki-mtl/ShogiArena/tree/main/src/shogiarena/_core/interfaces/dashboard/frontend)：画面とフロントエンドのテスト
- [docs/book](https://github.com/nyoki-mtl/ShogiArena/tree/main/docs/book)：ドキュメントの原稿とテーマ
