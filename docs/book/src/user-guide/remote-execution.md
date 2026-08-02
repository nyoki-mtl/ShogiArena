# リモート実行

> **Qualification完了**
>
> 実行spec、immutable deployment、job lifecycle、artifact検証を再構築し、
> 2026-07-31にproduction qualificationを完了しました。
> Production CLIからLinux x86_64 workerへのRemote tournament/SPSAを利用できます。

### Qualification status（2026-07-31）

Windows coordinatorからLinux x86_64 workerへのproduction-path試験では、
Remote tournament、Remote SPSA、same-host 2並列、CAS再利用・digest分離、
`preplaced`のremote hash検証、durable jobの重複操作、Remote SPSA archiveの
browser表示とzero-writeを確認しました。加えてLinux coordinator、2 endpoint/same-root、
実YaneuraOuのfull YAML parityと`usi_tunables`、SSH応答喪失、TERM無視、heartbeat停止、
result write失敗の実host fault matrixを完走しました。

## ユースケース

- **複数サーバーでの並列実行**：対局数が多い場合、複数のサーバーに分散して所要時間を短縮する
- **GPU サーバーの活用**：ニューラルネットワークエンジンを GPU サーバーで実行する
- **CI/CD 環境**：クラウド上のマシンでトーナメントを実行する

## 前提条件

### リモートサーバー側

1. **SSH アクセス**：公開鍵認証が設定されていること
2. **bash**：コマンドシェルとして bash が利用できること
3. **uv**：worker bundleが固定するCPython 3.12 runtimeを用意できること
4. **Linux x86_64**：初期Remote workerの対象platformであること

### ローカル側

1. **SSH クライアント**：`ssh` コマンドが利用できること
2. **ShogiArena**：ローカルに ShogiArena がインストール済みであること

> **Warning: Linux x86_64 以外の worker は非対応**
>
> リモート実行先は Linux x86_64 のみをqualification対象とします。
> Windows worker、macOS worker、Linux arm64 はpreflightで拒否されます。
> Coordinatorとローカル対局はWindows x86_64、Linux x86_64／arm64、
> macOS Intel／Apple Siliconで動作します。

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
| `operating_system` | `linux`。初期Remote workerでは他の値をreject |
| `architecture` | `x86_64`。初期Remote workerでは他の値をreject |
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
    instance_id: "remote1"
  
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

Instance assignmentは`system.instance_scheduling.policy`で明示します。

```yaml
system:
  instance_scheduling:
    policy: explicit  # local / explicit / auto
    required_tags: []
    allocation_timeout: 30
```

- `local`（既定）は全roleをlocalへ割り当てます。remote `instance_id`を指定した場合はfail closedとなり、`explicit`の明示が必要です。
- `explicit`は両roleの`instance_id`を必須とし、同じhealthy SSH instanceであることを検証します。
- `auto`はengine側の`instance_id`と併用できません。Linux x86_64、required tags、slot/engine capacity、health、draining状態を満たす候補からcurrent leaseが最小のworkerを選びます。

`auto`または`explicit`で候補・指定先が不適格な場合、localへfallbackしません。
選択したendpoint/deployment/jobはworker開始前にgame execution artifactへ保存し、resume時に一致を検証します。

## ファイル同期（Provisioning）

リモート実行時、エンジンバイナリや設定ファイルをリモートサーバーに同期する必要があります。

### プロビジョニングモード

| モード | 説明 |
| --- | --- |
| `cas` | 既定。endpoint/platform/kind/content digest単位のCASへ検証付きで配置 |
| `preplaced` | remote absolute pathとexpected SHA-256を検証して既配置resourceを参照 |

```bash
# content-addressed CAS（既定）
shogiarena run tournament tournament.yaml \
  --provision cas

# 既配置bookをfull SHA-256検証して使用
SHOGIARENA_REMOTE_PREPLACED_RESOURCES='{"<engine-name>-linux":{"path":"/opt/engines/engine","sha256":"<engineの64文字lowercase SHA-256>"},"<engine-name>-linux-resource-<book digest先頭12文字>":{"path":"/opt/books/user_book1.db","sha256":"<bookの64文字lowercase SHA-256>"}}' \
SHOGIARENA_REMOTE_BOOK_TRANSFER=preplaced \
SHOGIARENA_REMOTE_BOOK_PREPLACED_PATH=/opt/books/user_book1.db \
SHOGIARENA_REMOTE_BOOK_PREPLACED_SHA256=<64文字のlowercase SHA-256> \
  shogiarena run tournament tournament.yaml --provision preplaced
```

### 同期されるファイル

- エンジンバイナリ（`engine_path` で指定されたファイル）
- 設定ファイル（YAML）
- 開局集（`initial_positions.source` で指定されたファイル）
- 内蔵定跡 book（`BookDir + BookFile` で解決されたファイル。下記 policy に従う）

### 定跡 book の転送

YaneuraOu 系の `BookDir` / `BookFile` は、remote 実行では book file 単体を content-hash 名で worker 側に配置します。
巨大な book を意図せず転送しないよう、既定では 256 MiB を超える自動転送を明示エラーにします。
`BookFile: no_book`はファイルパスではなく定跡無効化のUSI値として扱います。
転送対象にはせず、sealed specとworkerへの`setoption name BookFile value no_book`に保持します。

| 環境変数 | 値 | 説明 |
| --- | --- | --- |
| `SHOGIARENA_REMOTE_BOOK_TRANSFER` | `auto` | 既定。`SHOGIARENA_REMOTE_BOOK_MAX_MB` 以下なら自動転送し、超過時は停止 |
| `SHOGIARENA_REMOTE_BOOK_TRANSFER` | `always` | サイズに関わらず content-hash 転送する |
| `SHOGIARENA_REMOTE_BOOK_TRANSFER` | `preplaced` | remote pathの存在とfull SHA-256一致をdispatch前に要求する |
| `SHOGIARENA_REMOTE_BOOK_MAX_MB` | 整数 | `auto` の上限 MiB。既定は `256` |
| `SHOGIARENA_REMOTE_BOOK_PREPLACED_PATH` | absolute POSIX path | `preplaced`で必須のremote file path |
| `SHOGIARENA_REMOTE_BOOK_PREPLACED_SHA256` | lowercase SHA-256 | `preplaced`で必須のexpected digest |
| `SHOGIARENA_REMOTE_PREPLACED_RESOURCES` | JSON object | `--provision preplaced`で必須。全artifact logical IDをabsolute remote pathとexpected SHA-256へ対応付ける |

Logical IDとdigestを手で組み立てる必要はありません。
次のコマンドはengine binaryとfile／directory resourceをhashし、そのまま環境変数へ設定できる
単一行JSONを返します。

```powershell
$env:SHOGIARENA_REMOTE_PREPLACED_RESOURCES = shogiarena worker-bundle preplaced-map `
  --engine engine-a C:\artifacts\linux-x86_64\engine-a /opt/engines/engine-a `
  --engine engine-b C:\artifacts\linux-x86_64\engine-b /opt/engines/engine-b `
  --resource engine-a C:\eval\engine-a /opt/eval/engine-a
```

`LOCAL_PATH`には、Remote workerで実行するLinux x86_64 binaryのlocal copyを指定します。

Coordinator上で動かすWindows `.exe`ではなく、`REMOTE_PATH`へ配置したbinaryと同じbytesを指定してください。

`--engine NAME LOCAL_PATH REMOTE_PATH`と`--resource ENGINE_NAME LOCAL_PATH REMOTE_PATH`は
必要な数だけ繰り返せます。
出力するdigestは実行時のfile／canonical directory tree検証と同じ実装で計算します。

```bash
# 大型 book を明示的に転送する
SHOGIARENA_REMOTE_BOOK_TRANSFER=always \
  shogiarena run tournament tournament.yaml --provision cas

# worker 側に事前配置した book を使う
SHOGIARENA_REMOTE_PREPLACED_RESOURCES='{"<engine-name>-linux":{"path":"/opt/engines/engine","sha256":"<engineの64文字lowercase SHA-256>"},"<engine-name>-linux-resource-<book digest先頭12文字>":{"path":"/opt/books/user_book1.db","sha256":"<bookの64文字lowercase SHA-256>"}}' \
SHOGIARENA_REMOTE_BOOK_TRANSFER=preplaced \
SHOGIARENA_REMOTE_BOOK_PREPLACED_PATH=/opt/books/user_book1.db \
SHOGIARENA_REMOTE_BOOK_PREPLACED_SHA256=<64文字のlowercase SHA-256> \
  shogiarena run tournament tournament.yaml --provision preplaced
```

`preplaced` は全artifact logical IDのmappingが存在し、local resource digest、expected digest、
remote fileまたはcanonical directory tree digestがすべて一致した場合だけdispatchします。
`<engine-name>`は各roleで解決されたengine名です。Engine binaryのlogical IDは
`<engine-name>-linux`、path resourceは
`<engine-name>-linux-resource-<local content SHA-256の先頭12文字>`です。
Black/Whiteでengine名が異なる場合は、両roleのengine binaryとpath resourceをすべてmappingへ列挙します。
不足したIDはpreflightの`preplaced resource contract is missing logical ID: ...`で確認できますが、
通常は`worker-bundle preplaced-map`の出力を使ってください。
Directory treeはplatform差を除くためdirectoryを`0755`、regular fileを`0644`へ正規化してdigest化します。
`preplaced` directoryもこのmode契約を満たす必要があります。
旧`--provision none`は検証を省略するため削除されました。

### Remote設定の移行表

| 旧設定・挙動 | 現行契約 |
| --- | --- |
| `--provision none` | `--provision preplaced`と全resourceのabsolute path / expected digest |
| `--provision force` | `--provision cas`。immutable endpoint-aware CASを再検証して利用 |
| CWD Git remote / shared checkout | wheel・lock・manifestから作るimmutable worker deployment |
| top-level `configs/` overlay | `GameExecutionSpec`が列挙するartifactだけをbundleへ含める |
| 暗黙のremote選択やlocal fallback | `system.instance_scheduling.policy`を`local` / `explicit` / `auto`で明示 |
| Windows/macOS worker | Linux x86_64 workerへ移行 |

旧値にsilent aliasはありません。Validation errorを確認し、元のrun configをbackupしてから
上表へ明示的に書き換えてください。

## Worker bundle、deployment、job

Worker bundleはwheel、lock、manifest、宣言済みartifactから決まります。Endpoint/platform/
content digest単位でstagingを検証してatomic publishし、公開済みdeploymentを上書きしません。
Resume時のbundle不一致はexpected digestとactual digestを表示します。
Sealed `remote-worker-bundle.zip`を復元できない場合は、`--no-resume`で新しいrunを開始してください。

`GameExecutionSpec.minimum_worker_version`はRemote workerだけでなくLocal実行でもengine起動前に検証します。

各対局attemptは独立したjob IDとjob directoryを持ちます。Prepare/start/status/cancel/collect/ackは
idempotentで、coordinator切断後は同じjob IDをstatus/collectします。新しいjobを推測作成しません。
Prepare/resultの応答喪失は同じ操作を1回だけ再試行し、startの応答喪失は同じjobのstatusを
確認して`prepared`の場合だけ再送します。再接続後も失敗した場合は結果を推測せずleaseを保持します。
Heartbeat stale、process-group mismatch、startup/outer deadline、TERM/KILL escalation、
collect/ack、orphan reaperの診断はjob statusとrun failure artifactへ残ります。

Qualification対象はLinux x86_64 workerです。未対応platformや未検証のcloud固有障害まで
成功を保証するものではないため、各runのcompletion、participation、artifact digestを確認してください。

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
`slots: null`はstartup health preflightで取得したCPU countを使います。
preflightはdashboardの有効/無効に依存せずrun開始時に実行され、CPU countを取得できない場合はunknown capacityとしてdispatch前に停止します。
算出方法は [トーナメント](tournaments.md#並列数とインスタンス容量) を参照してください。

### エンジン数の制限

`max_engines` で、同時起動できるエンジンプロセス数を制限します。
省略した場合はresolved slot capacityを上限として使います。
Capacityが一時的に使用中の場合は`allocation_timeout`まで待機し、期限到達時は対象instanceとgame IDを含むerrorで停止します。

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

- CASは同一endpoint・同一digestをremote hash検証後に再利用する
- 大きな既配置resourceは`preplaced`のpath/digest contractを使う

## 参考資料

- [トーナメントガイド](tournaments.md)：トーナメント設定の詳細
- [エンジン設定](engine-configuration.md)：エンジン設定ファイルの書き方
