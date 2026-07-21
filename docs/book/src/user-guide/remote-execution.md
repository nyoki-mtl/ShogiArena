# リモート実行

ShogiArena は SSH 経由でリモートサーバー上でエンジンを実行し、対局を分散実行できます。

## ユースケース

- **複数サーバーでの並列実行**：対局数が多い場合、複数のサーバーに分散して所要時間を短縮する
- **GPU サーバーの活用**：ニューラルネットワークエンジンを GPU サーバーで実行する
- **CI/CD 環境**：クラウド上のマシンでトーナメントを実行する

## 前提条件

### リモートサーバー側

1. **SSH アクセス**：公開鍵認証が設定されていること
2. **bash**：コマンドシェルとして bash が利用できること
3. **Python 3.11+**：リモート側に Python がインストールされていること
4. **エンジンバイナリ**：リモート側にエンジンがビルドされ、配置されていること

### ローカル側

1. **SSH クライアント**：`ssh` コマンドが利用できること
2. **ShogiArena**：ローカルに ShogiArena がインストール済みであること

> **Warning: Windows リモート実行は非対応**
>
> リモート実行先は Linux/macOS のみサポートします。
> ローカル（orchestrator）は Windows でも動作します。

## インスタンス設定ファイル

リモート実行では、インスタンス定義を別 YAML に切り出し、それを run 設定の `instances:` に渡します。

インスタンス設定ファイル自体は、複数インスタンスを並べるリスト形式ではありません。
1 ファイル 1 インスタンスとして書くか、`hosts:` による展開方式を使います。

### 基本的な SSH 設定

```yaml
# examples/configs/resources/instances/ssh_example.yaml
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
| `slots` | engine thread / ponder から見積もる同時実行容量 |
| `max_engines` | 同時起動できる engine process 数の上限 |
| `is_strict_host_key_checking` | host key 検証を厳格に行うか |
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

この場合、`worker-001`、`worker-002` のような名前で展開されます。

### ローカル実行用

```yaml
name: "local"
type: "local"
slots: 4
```

## トーナメント実行

### tournament.yaml 側での指定

CLI に `--instances` オプションはありません。
使用するインスタンスは、run 設定ファイルの `instances:` で指定します。

```yaml
# tournament.yaml
experiment_name: "remote_tournament"

instances:
  - examples/configs/resources/instances/local_example.yaml
  - examples/configs/resources/instances/ssh_example.yaml

engines:
  - name: "EngineA"
    engine_path: "engine_a.yaml"
    instance_id: "remote1"
  
  - name: "EngineB"
    engine_path: "engine_b.yaml"
    instance_id: "local"
  
  - name: "EngineC"
    engine_path: "engine_c.yaml"
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

`instance_id` を省略した場合、ShogiArena が利用可能なインスタンスへ自動的に割り当てます。

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
- 内蔵定跡 book（`BookDir + BookFile` で解決されたファイル。下記 policy に従う）

### 定跡 book の転送

YaneuraOu 系の `BookDir` / `BookFile` は、remote 実行では book file 単体を content-hash 名で worker 側に配置します。
巨大な book を意図せず転送しないよう、既定では 256 MiB を超える自動転送を明示エラーにします。

| 環境変数 | 値 | 説明 |
| --- | --- | --- |
| `SHOGIARENA_REMOTE_BOOK_TRANSFER` | `auto` | 既定。`SHOGIARENA_REMOTE_BOOK_MAX_MB` 以下なら自動転送し、超過時は停止 |
| `SHOGIARENA_REMOTE_BOOK_TRANSFER` | `always` | サイズに関わらず content-hash 転送する |
| `SHOGIARENA_REMOTE_BOOK_TRANSFER` | `preplaced` | 転送せず、worker 側に同じ path の book が事前配置されている前提で参照する |
| `SHOGIARENA_REMOTE_BOOK_MAX_MB` | 整数 | `auto` の上限 MiB。既定は `256` |

```bash
# 大型 book を明示的に転送する
SHOGIARENA_REMOTE_BOOK_TRANSFER=always \
  shogiarena run tournament tournament.yaml --provision force

# worker 側に事前配置した book を使う
SHOGIARENA_REMOTE_BOOK_TRANSFER=preplaced \
  shogiarena run tournament tournament.yaml --provision none
```

`preplaced` では転送しませんが、local 側で解決した book fingerprint は provenance に記録されます。
記録された fingerprint と worker 側の配置が一致していることは、運用側で確認してください。

## SSH 認証の設定

### 公開鍵認証

リモート実行では公開鍵認証を使用します。
パスワード認証は非対応です。

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

`slots` は、各インスタンスで同時に使える実行容量です。

ShogiArena は engine の `Threads` / `USI_Threads` と `Ponder` / `USI_Ponder` から必要 slot 数を見積もり、`tournament.num_parallel` 分の pending games が `slots` と `max_engines` に収まるかを開始前に検査します。
見積もりを自動に任せたい場合は `slots: null` を使います。
算出方法は [トーナメント](tournaments.md#並列数とインスタンス容量) を参照してください。

### エンジン数の制限

`max_engines` で、同時起動できるエンジンプロセス数を制限します。
省略した場合は上限なしとして扱われます。

## トラブルシューティング

### SSH 接続エラー

#### ホストキー検証エラー

```
Host key verification failed
```

**原因**：リモートサーバーのホストキーが `~/.ssh/known_hosts` に登録されていない。

**解決**：
```bash
# 手動で接続してホストキーを登録
ssh user@remote-server
```

#### 認証エラー

```
Permission denied (publickey)
```

**原因**：公開鍵認証が正しく設定されていない。

**解決**：
1. 公開鍵がリモートの `~/.ssh/authorized_keys` に登録されているか確認
2. 秘密鍵のパーミッションを確認（`chmod 600 ~/.ssh/id_rsa`）
3. SSH エージェントに鍵が登録されているか確認（`ssh-add -l`）

### ファイル同期エラー

#### プロジェクトルートが存在しない

```
Remote project_root does not exist: ~/shogiarena
```

**解決**：
```bash
# リモートでディレクトリを作成
ssh user@remote-server "mkdir -p ~/shogiarena"
```

#### エンジンバイナリが見つからない

**原因**：リモート側にエンジンがビルドされていない。

**解決**：
1. リモート側でエンジンをビルド
2. エンジン設定の `engine_path` を確認

### パフォーマンス問題

#### 対局が遅い

- **リソース不足**：`slots` や `max_engines` を減らし、サーバーの負荷を下げる

#### 同期に時間がかかる

- `--provision none` で同期を無効化する（ファイルは事前に配置しておく）
- 定跡データベースなどの大きなファイルは、事前にリモートへ配置しておく

## 参考資料

- [トーナメントガイド](tournaments.md)：トーナメント設定の詳細
- [エンジン設定](engine-configuration.md)：エンジン設定ファイルの書き方
