# 最初のトーナメント

このチュートリアルでは、同じエンジンを別オプションで 2 つ登録し、強さ違いの round-robin を動かします。

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

トーナメント側の `options` は、参照先のエンジン YAML にマージされます。ここでは同じ実行ファイルを使い、スレッド数とハッシュだけ変えています。

## 実行

```bash
shogiarena run tournament first_tournament.yaml --dry-run
shogiarena run tournament first_tournament.yaml
```

実行中は `http://localhost:8080` を開きます。終了後は run ディレクトリを指定して再表示できます。

```bash
shogiarena dashboard serve --run-dir /path/to/run
```

## 確認するポイント

- `Tournament`: 順位表、勝敗、Elo 推定
- `Games`: 個別対局、棋譜、結果フィルタ
- `Engines`: 実際に使われたオプション
- `Live View`: 進行中対局の盤面と時計
- `Rules`: 持ち時間や adjudication 設定

## 次のステップ

- [トーナメント](../user-guide/tournaments.md)
- [エンジン設定](../user-guide/engine-configuration.md)
- [ダッシュボード](../user-guide/dashboard.md)
