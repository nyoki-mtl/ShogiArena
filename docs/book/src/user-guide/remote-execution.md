# リモート実行

ShogiArena は SSH 経由でリモートサーバー上でエンジンを実行し、対局を分散実行できます。

## ユースケース

- **複数サーバーでの並列実行**: 対局数が多い場合、複数のサーバーに分散して高速化
- **GPU サーバーの活用**: ニューラルネットワークエンジンを GPU サーバーで実行
- **CI/CD 環境**: クラウド上のマシンでトーナメントを実行

## 前提条件

### リモートサーバー側

1. **SSH アクセス**: 公開鍵認証が設定されていること
2. **bash**: コマンドシェルとして bash が利用可能
3. **Python 3.11+**: リモート側に Python がインストールされていること
4. **エンジンバイナリ**: リモート側にエンジンがビルド・配置されていること

### ローカル側

1. **SSH クライアント**: `ssh` コマンドが利用可能
2. **ShogiArena**: ローカルに ShogiArena がインストール済み

> **Warning: Windows リモート実行は非対応**
>
> リモート実行先は Linux/macOS のみサポートします。
> ローカル（orchestrator）は Windows でも動作します。


## インスタンス設定ファイル

リモート実行では、インスタンス定義を別 YAML に切り出し、それを run 設定の `instances:` に渡します。

現在のフォーマットは **`instances:` リスト方式ではありません**。1 ファイル 1 インスタンス、または `hosts:` 展開方式を使います。

### 基本的な SSH 設定

```yaml
# configs/resources/instances/ssh_remote.yaml
name: "remote1"
type: "ssh"
hosts:
  - "192.168.1.10"
user: "username"
identity_file: "~/.ssh/id_rsa"
project_root: "$HOME/ShogiArena-remote"
slots: 4
is_strict_host_key_checking: true
```

#### 主なフィールド

| フィールド | 説明 |
| --- | --- |
| `name` | インスタンス名（省略時はファイル名 stem） |
| `type` | `local` または `ssh` |
| `hosts` | SSH 接続先のリスト（SSH 時は必須） |
| `user` | SSH ユーザー |
| `identity_file` | 秘密鍵パス |
| `port` | SSH ポート。省略時は 22 |
| `project_root` | リモート側の作業ディレクトリ（省略時は `~/ShogiArena-remote`） |
| `slots` | 同時実行可能な対局数 |
| `is_strict_host_key_checking` | host key 検証を厳格に行うか（デフォルト: true） |
| `tags` | インスタンスのタグ（任意） |

### 同じ設定を複数ホストへ展開する

```yaml
name: "worker"
type: "ssh"
user: "user"
identity_file: "~/.ssh/id_rsa"
project_root: "$HOME/ShogiArena-remote"
slots: 8
hosts:
  - server1.example.com
  - server2.example.com
```

この場合、`worker-001`, `worker-002` のような名前で展開されます。

### ローカル実行用

```yaml
name: "local"
type: "local"
slots: 4
```

## トーナメント実行

### tournament.yaml 側での指定

CLI に `--instances` オプションはありません。run 設定ファイルの `instances:` で指定します。

```yaml
# tournament.yaml
experiment_name: "remote_tournament"

instances:
  - configs/resources/instances/local.yaml
  - configs/resources/instances/ssh_remote.yaml

engines:
  - name: "EngineA"
    engine_path: "configs/engine/engine_a.yaml"
    instance_id: "remote1"
  
  - name: "EngineB"
    engine_path: "configs/engine/engine_b.yaml"
    instance_id: "local"
  
  - name: "EngineC"
    engine_path: "configs/engine/engine_c.yaml"
    instance_id: "remote1"

tournament:
  scheduler: round_robin
  games_per_pair: 100
  num_parallel: 8

rules:
  time_control:
    time_ms: 30000
    increment_ms: 300
```

`instance_id` を指定しない場合、自動的に利用可能なインスタンスに割り当てられます。

## ファイル同期（Provisioning）

リモート実行時、エンジンバイナリや設定ファイルをリモートサーバーに同期する必要があります。

### プロビジョニングモード

| モード | 説明 |
| --- | --- |
| `none` | 同期しない（リモート側に既にファイルがあることを前提） |
| `force` | 毎回強制的に同期 |

```bash
# 毎回同期
shogiarena run tournament tournament.yaml \
  --provision force

# 同期しない
shogiarena run tournament tournament.yaml \
  --provision none
```

### 同期されるファイル

- エンジンバイナリ（`engine_path` で指定されたファイル）
- 設定ファイル（YAML）
- 開局集（`initial_positions.source` で指定されたファイル）

### 同期対象の除外

`.gitignore` スタイルで同期対象を制御できます（将来実装予定）。

## SSH 認証の設定

### 公開鍵認証

リモート実行では公開鍵認証を使用します。パスワード認証は非対応です。

#### 1. 鍵ペアの生成（未作成の場合）

```bash
ssh-keygen -t rsa -b 4096 -C "your_email@example.com"
```

#### 2. 公開鍵をリモートサーバーに登録

```bash
ssh-copy-id -i ~/.ssh/id_rsa.pub user@remote-server
```

#### 3. 接続テスト

```bash
ssh -i ~/.ssh/id_rsa user@remote-server
```

### SSH エージェントの使用

複数のサーバーに接続する場合、SSH エージェントを使用すると便利です。

```bash
# SSH エージェント起動
eval $(ssh-agent)

# 秘密鍵を登録
ssh-add ~/.ssh/id_rsa

# インスタンス設定ファイルで identity_file を省略可能
```

## 負荷分散

### スロット数の設定

`slots` は、各インスタンスで同時に実行できる対局数です。

各インスタンス YAML の `slots` が、そのインスタンスで同時に走る対局数です。自動扱いにしたい場合は `slots: null` を使います。

### エンジン数の制限

`max_engines` で、同時起動できるエンジンプロセス数を制限します。省略した場合は上限なしとして扱われます。

## トラブルシューティング

### SSH 接続エラー

#### ホストキー検証エラー

```
Host key verification failed
```

**原因**: リモートサーバーのホストキーが `~/.ssh/known_hosts` に登録されていない。

**解決**:
```bash
# 手動で接続してホストキーを登録
ssh user@remote-server
```

#### 認証エラー

```
Permission denied (publickey)
```

**原因**: 公開鍵認証が正しく設定されていない。

**解決**:
1. 公開鍵がリモートの `~/.ssh/authorized_keys` に登録されているか確認
2. 秘密鍵のパーミッションを確認（`chmod 600 ~/.ssh/id_rsa`）
3. SSH エージェントに鍵が登録されているか確認（`ssh-add -l`）

### ファイル同期エラー

#### プロジェクトルートが存在しない

```
Remote project_root does not exist: ~/shogiarena
```

**解決**:
```bash
# リモートでディレクトリを作成
ssh user@remote-server "mkdir -p ~/shogiarena"
```

#### エンジンバイナリが見つからない

**原因**: リモート側にエンジンがビルドされていない。

**解決**:
1. リモート側でエンジンをビルド
2. エンジン設定の `engine_path` を確認

### パフォーマンス問題

#### 対局が遅い

- **リソース不足**: `slots` や `max_engines` を減らして、サーバーの負荷を軽減

#### 同期に時間がかかる

- `--provision none` を使用して同期を無効化（事前にファイルを配置しておく）
- 大きなファイル（定跡データベースなど）は事前にリモートに配置

## 参考資料

- [トーナメントガイド](tournaments.md): トーナメント設定の詳細
- [エンジン設定](engine-configuration.md): エンジン設定ファイルの書き方
- [技術ドキュメント - Instances](../technical/instances.md): インスタンスシステムの設計詳細
