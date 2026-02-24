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
3. **Python 3.10+**: リモート側に Python がインストールされていること
4. **エンジンバイナリ**: リモート側にエンジンがビルド・配置されていること

### ローカル側

1. **SSH クライアント**: `ssh` コマンドが利用可能
2. **ShogiArena**: ローカルに ShogiArena がインストール済み

> **Warning: Windows リモート実行は非対応**
>
> リモート実行先は Linux/macOS のみサポートします。
> ローカル（orchestrator）は Windows でも動作します。


## インスタンス設定ファイル

リモート実行するには、インスタンス設定ファイル（YAML）を作成します。

### 基本的な SSH 設定

```yaml
# configs/resources/instances/ssh_remote.yaml
instances:
  - instance_id: "remote1"
    type: "ssh"
    ssh:
      host: "192.168.1.10"
      user: "username"
      key_file: "~/.ssh/id_rsa"
    project_root: "~/shogiarena"
    slots: 4
    max_engines: 8
```

#### フィールドの説明

| フィールド | 説明 |
| --- | --- |
| `instance_id` | インスタンスの一意な識別子 |
| `type` | `"ssh"` または `"local"` |
| `ssh.host` | リモートサーバーのホスト名または IP アドレス |
| `ssh.user` | SSH ログインユーザー名 |
| `ssh.key_file` | SSH 秘密鍵のパス（ローカル側のパス） |
| `ssh.port` | SSH ポート（デフォルト: 22） |
| `project_root` | リモート側の作業ディレクトリ |
| `slots` | 同時実行可能な対局数（0 または負の値で自動検出） |
| `max_engines` | 同時起動可能なエンジン数（0 で無制限） |

### 複数サーバーの設定

```yaml
# configs/resources/instances/multi_ssh.yaml
instances:
  - instance_id: "server1"
    type: "ssh"
    ssh:
      host: "server1.example.com"
      user: "user"
      key_file: "~/.ssh/id_rsa"
    project_root: "~/arena"
    slots: 8
    max_engines: 16
  
  - instance_id: "server2"
    type: "ssh"
    ssh:
      host: "server2.example.com"
      user: "user"
      key_file: "~/.ssh/id_rsa"
    project_root: "~/arena"
    slots: 8
    max_engines: 16
  
  - instance_id: "local"
    type: "local"
    slots: 4
    max_engines: 8
```

### GPU サーバーの設定

```yaml
instances:
  - instance_id: "gpu_server"
    type: "ssh"
    ssh:
      host: "gpu.example.com"
      user: "gpuuser"
      key_file: "~/.ssh/id_rsa"
    project_root: "~/shogiarena"
    slots: 2  # GPU が 2 基搭載
    max_engines: 4
    env:
      CUDA_VISIBLE_DEVICES: "0,1"
```

## トーナメント実行

### インスタンス設定を指定

```bash
shogiarena run tournament tournament.yaml \
  --instances configs/resources/instances/multi_ssh.yaml
```

### エンジンとインスタンスの割り当て

トーナメント設定ファイル内で、どのエンジンをどのインスタンスで実行するか指定できます。

```yaml
# tournament.yaml
experiment_name: "remote_tournament"

engines:
  - name: "EngineA"
    engine_path: "configs/engine/engine_a.yaml"
    instance_id: "server1"  # server1 で実行
  
  - name: "EngineB"
    engine_path: "configs/engine/engine_b.yaml"
    instance_id: "server2"  # server2 で実行
  
  - name: "EngineC"
    engine_path: "configs/engine/engine_c.yaml"
    instance_id: "local"    # ローカルで実行

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
| `auto` | 必要に応じて同期（デフォルト） |
| `force` | 毎回強制的に同期 |

```bash
# 毎回同期
shogiarena run tournament tournament.yaml \
  --instances ssh.yaml \
  --provision force

# 同期しない
shogiarena run tournament tournament.yaml \
  --instances ssh.yaml \
  --provision none
```

### 同期されるファイル

- エンジンバイナリ（`path` で指定されたファイル）
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

# インスタンス設定ファイルで key_file を省略可能
```

## 負荷分散

### スロット数の設定

`slots` は、各インスタンスで同時に実行できる対局数です。

```yaml
instances:
  - instance_id: "server1"
    slots: 8  # 8 対局を同時実行
  
  - instance_id: "server2"
    slots: 4  # 4 対局を同時実行
```

自動検出（`slots: 0`）の場合、CPU コア数が使用されます。

### エンジン数の制限

`max_engines` で、同時起動できるエンジンプロセス数を制限します。

```yaml
instances:
  - instance_id: "server1"
    slots: 8
    max_engines: 16  # 最大 16 プロセス
```

0 または負の値で無制限になります。

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
2. エンジン設定の `path` をリモート側の絶対パスに変更

### パフォーマンス問題

#### 対局が遅い

- **ネットワーク遅延**: リモートサーバーとの通信が遅い場合、SSH の圧縮を有効化
  ```yaml
  ssh:
    compression: true
  ```

- **リソース不足**: `slots` や `max_engines` を減らして、サーバーの負荷を軽減

#### 同期に時間がかかる

- `--provision none` を使用して同期を無効化（事前にファイルを配置しておく）
- 大きなファイル（定跡データベースなど）は事前にリモートに配置

## 高度な設定

### カスタム SSH オプション

```yaml
instances:
  - instance_id: "remote"
    type: "ssh"
    ssh:
      host: "remote-server"
      user: "user"
      key_file: "~/.ssh/id_rsa"
      port: 2222
      connect_timeout: 30
      compression: true
```

### 環境変数の設定

リモートエンジンに環境変数を渡すことができます。

```yaml
instances:
  - instance_id: "gpu_server"
    type: "ssh"
    ssh:
      host: "gpu.example.com"
      user: "user"
      key_file: "~/.ssh/id_rsa"
    project_root: "~/arena"
    env:
      CUDA_VISIBLE_DEVICES: "0"
      LD_LIBRARY_PATH: "/usr/local/cuda/lib64"
```

## 参考資料

- [トーナメントガイド](tournaments.md): トーナメント設定の詳細
- [エンジン設定](engine-configuration.md): エンジン設定ファイルの書き方
- [技術ドキュメント - Instances](../technical/instances.md): インスタンスシステムの設計詳細
