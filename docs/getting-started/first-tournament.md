# 初めてのトーナメント

このチュートリアルでは、実際に複数のエンジンを使ったトーナメントを実行し、結果を分析する方法を学びます。

## このチュートリアルで学ぶこと

- 複数エンジンの設定方法
- さまざまなトーナメント方式の使い方
- 持ち時間の設定とその影響
- ダッシュボードでの結果確認

## ステップ 1: エンジンの準備

USI プロトコルに対応した将棋エンジンを用意し、設定ファイル (`.yaml`) を作成します。

**configs/engine/yaneuraou.yaml**:
```yaml
name: "YaneuraOu"
path: "/path/to/YaneuraOu"
options:
  Threads: 2
  USI_Hash: 256
```

## ステップ 2: トーナメント設定

### ラウンドロビン方式

すべてのエンジンが互いに対局する方式です。

**configs/arena/round_robin.yaml**:
```yaml
experiment_name: "first_tournament"

engines:
  - engine_config: "configs/engine/yaneuraou.yaml"
    name: "YaneuraOu_Strong"
    options: { Threads: 4 }
  - engine_config: "configs/engine/yaneuraou.yaml"
    name: "YaneuraOu_Weak"
    options: { Threads: 1 }

tournament:
  scheduler: round_robin
  games_per_pair: 10

rules:
  time_control:
    time_ms: 10000
    increment_ms: 1000

dashboard:
  enabled: true
```

### トーナメントの実行

```bash
shogiarena run tournament configs/arena/round_robin.yaml
```

## ステップ 3: ダッシュボードでの確認

実行時に表示される URL (通常 `http://localhost:8080`) にアクセスします。

- **Overview**: 進行状況と勝率
- **Engines**: レーティング推移
- **Games**: 個別の対局結果と棋譜

## 次のステップ

- **[Tournaments](../user-guide/tournaments.md)** - 設定項目の詳細リファレンス
- **[SPSA Tuning](../user-guide/spsa.md)** - パラメータチューニングの方法
