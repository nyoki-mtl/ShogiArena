# 最初のトーナメント

同じエンジンを別オプションで 2 つ登録し、設定違いによる強さの差を round-robin で測ります。
設定ファイルの基本構造は[クイックスタート](quick-start.md)と同じなので、ここでは違いのある箇所だけを説明します。

## エンジン設定

`engines/yaneuraou.yaml`:

```yaml
name: "YaneuraOu"
engine_path: "/path/to/YaneuraOu"
options:
  Threads: 2
  USI_Hash: 256
```

## トーナメント設定

`first_tournament.yaml`:

```yaml
experiment_name: "first_tournament"

engines:
  - engine_path: "engines/yaneuraou.yaml"
    name: "YaneuraOu_Strong"
    options:
      Threads: 4
      USI_Hash: 1024
  - engine_path: "engines/yaneuraou.yaml"
    name: "YaneuraOu_Light"
    options:
      Threads: 1
      USI_Hash: 128

tournament:
  scheduler: round_robin
  games_per_pair: 10
  num_parallel: 2

rules:
  time_control:
    time_ms: 10000
    increment_ms: 1000
  adjudication:
    enable_max_plies: true
    max_plies: 320

dashboard:
  enabled: true
  api_port: 8080
```

トーナメント側の `options` は、参照先のエンジン YAML にマージされます。
ここでは同じ実行ファイルを参照したまま、`name` で別エンジンとして区別し、スレッド数とハッシュだけを変えています。

## 実行

```bash
shogiarena run tournament first_tournament.yaml --dry-run
shogiarena run tournament first_tournament.yaml
```

実行中は `http://localhost:8080` を開きます。
終了後の run ディレクトリからの再表示や結果集計は、[クイックスタート](quick-start.md)と同じ手順です。

## ダッシュボードで確認するポイント

- `Tournament`：順位表、勝敗、Elo 推定
- `Games`：個別対局、棋譜、結果フィルタ
- `Engines`：実際に使われたオプション
- `Live View`：進行中対局の盤面と時計
- `Rules`：持ち時間や adjudication 設定

実行ファイルは両者で共通なので、順位表に出る差はスレッド数とハッシュの違いに由来します。
ただし 10 局程度では偶然の振れが大きいため、差を見積もるには対局数を増やす必要があります。
順位表を読む前に `Engines` で両者のオプションが意図どおり分かれているかを確認しておくと、マージ結果の取り違えを避けられます。

## 次のステップ

- [トーナメント](../user-guide/tournaments.md)：スケジューラと並列数の詳細
- [エンジン設定](../user-guide/engine-configuration.md)：エンジン YAML の全項目
- [ダッシュボード](../user-guide/dashboard.md)：画面ごとの見方
