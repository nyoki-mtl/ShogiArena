# 初めてのトーナメント

このチュートリアルでは、同じエンジン設定を 2 つのバリエーションとして使い、最小構成の round-robin を動かします。

## このチュートリアルで学ぶこと

- エンジン YAML の書き方
- tournament YAML の最小構成
- `--dry-run` での検証
- ダッシュボードと run ディレクトリの見方

## ステップ 1: エンジン YAML を作る

`configs/engine/yaneuraou.yaml`:

```yaml
name: "YaneuraOu"
engine_path: "/path/to/YaneuraOu"
options:
  Threads: 2
  USI_Hash: 256
```

## ステップ 2: tournament YAML を作る

`configs/arena/round_robin.yaml`:

```yaml
experiment_name: "first_tournament"

engines:
  - engine_path: "configs/engine/yaneuraou.yaml"
    name: "YaneuraOu_Strong"
    options:
      Threads: 4
      USI_Hash: 1024
  - engine_path: "configs/engine/yaneuraou.yaml"
    name: "YaneuraOu_Weak"
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

dashboard:
  enabled: true
  api_port: 8080
```

同じ `engine_path` を参照しつつ、トーナメント側で `options` を上書きして強さ違いのバリエーションを作っています。

## ステップ 3: まずは検証する

```bash
shogiarena run tournament configs/arena/round_robin.yaml --dry-run
```

## ステップ 4: 実行する

```bash
shogiarena run tournament configs/arena/round_robin.yaml
```

結果は通常 `output_dir/runs/first_tournament-<hash8>/YYYYMMDDHHMMSS/` に保存されます。

## ステップ 5: ダッシュボードで確認する

実行中は `http://localhost:8080` を開きます。

- `Tournament`: 勝敗、Elo、対戦表
- `Games`: 個別の棋譜と結果
- `Engines`: 実際に使われたエンジン設定
- `Live View`: 実行中の対局盤面

終了後に見直す場合:

```bash
shogiarena dashboard serve --config configs/arena/round_robin.yaml
```

## 次のステップ

- [トーナメントの実行](../user-guide/tournaments.md)
- [エンジン設定ファイル](../user-guide/engine-configuration.md)
- [ダッシュボードガイド](../user-guide/dashboard.md)
