# リモート実行

SSH接続したLinux x86_64のマシンで、トーナメントやSPSAを実行できます。
複数のマシンを登録すれば、対局を分散して進められます。

## 前提条件

### リモートサーバー側

- Linux x86_64
- SSHの公開鍵認証が設定済みであること
- `bash`が使えること
- `uv`でCPython 3.12の実行環境を用意できること

### ローカル側

1. **SSH クライアント**：`ssh` コマンドが利用できること
2. **ShogiArena**：ローカルに ShogiArena がインストール済みであること

ShogiArenaを操作するマシンとローカル対局には、Windows x86_64、Linux x86_64／arm64、macOS Intel／Apple Siliconを使えます。
リモートの実行先はLinux x86_64のみです。

## インスタンス設定ファイル

リモート実行では、実行先ごとにYAMLファイルを作り、対局設定の`instances:`で参照します。

インスタンス設定ファイル自体は、複数インスタンスを並べるリスト形式ではありません。
1 ファイル 1 インスタンスとして書くか、`hosts:` による展開方式を使います。

### 基本的な SSH 設定

```yaml
# examples/configs/resources/instances/ssh_example.yaml
name: "remote1"
type: "ssh"
host: "192.168.1.10"
user: "username"
identity_file: "~/.ssh/id_rsa"
project_root: "$HOME/ShogiArena-remote"
slots: 4
is_strict_host_key_checking: true
```

#### 主なフィールド

| フィールド | 説明 |
| --- | --- |
| `name` | インスタンス名（省略時は拡張子を除いたファイル名） |
| `type` | `local` または `ssh` |
| `host` | SSH接続先のホスト名またはIPアドレス |
| `hosts` | 同じ設定を複数の接続先に展開する場合のリスト |
| `user` | SSH ユーザー |
| `identity_file` | 秘密鍵パス |
| `port` | SSH ポート。省略時は 22 |
| `project_root` | リモート側の作業ディレクトリ（省略時は `~/ShogiArena-remote`） |
| `slots` | エンジンのスレッド数やponderに応じた同時実行容量 |
| `max_engines` | 同時に起動できるエンジン数の上限 |
| `operating_system` | `linux`のみ対応 |
| `architecture` | `x86_64`のみ対応 |
| `is_strict_host_key_checking` | SSHのホストキーを検証するか |
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

使用するインスタンスを、対局設定ファイルの`instances:`で指定します。

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
  num_parallel: 1

rules:
  time_control:
    time_ms: 30000
    increment_ms: 300

system:
  instance_scheduling:
    policy: explicit
```

対局をどのマシンへ割り当てるかは、`system.instance_scheduling.policy`で指定します。

```yaml
system:
  instance_scheduling:
    policy: explicit  # local / explicit / auto
    required_tags: []
    allocation_timeout: 30
```

- `local`（既定）は両方のエンジンをローカルで実行します。リモートの`instance_id`を指定するときは`explicit`を使ってください。
- `explicit`では両方のエンジンに`instance_id`が必要です。同じ、稼働可能なSSHインスタンスを指定してください。
- `auto`は条件を満たすマシンのうち、使用中の割り当てが最も少ない実行先を選びます。タグ、容量、稼働状態も検査します。エンジン側の`instance_id`とは併用できません。

`auto`や`explicit`で実行先を確保できない場合は停止します。
代わりにローカルで実行することはありません。

## エンジンと評価関数の配置

リモート実行時、エンジンバイナリや設定ファイルをリモートサーバーに同期する必要があります。

### プロビジョニングモード

| モード | 説明 |
| --- | --- |
| `cas` | 既定。ファイルをハッシュ値ごとに保存するキャッシュへ転送し、内容を検証する |
| `preplaced` | 配置済みファイルの絶対パスとSHA-256を検証して使う |

```bash
shogiarena run tournament tournament.yaml --provision cas
```

`preplaced`を使う場合は、以下の手順で配置済みファイルの一覧を設定してから実行します。

```bash
shogiarena run tournament tournament.yaml --provision preplaced
```

### 同期されるファイル

- エンジンバイナリ（`engine_path` で指定されたファイル）
- 設定ファイル（YAML）
- 開局集（`initial_positions.source` で指定されたファイル）
- 内蔵定跡（`BookDir + BookFile` で解決されたファイル。下記の設定に従う）

### 定跡ファイルの転送

YaneuraOuの`BookDir`と`BookFile`で指定する定跡ファイルは、ハッシュ値を名前にしてリモートへ転送します。
既定では、256 MiBを超えるファイルの自動転送を停止します。
`BookFile: no_book`はファイルパスではなく定跡無効化のUSI値として扱います。
転送せず、エンジンにも`no_book`を渡します。

| 環境変数 | 値 | 説明 |
| --- | --- | --- |
| `SHOGIARENA_REMOTE_BOOK_TRANSFER` | `auto` | 既定。`SHOGIARENA_REMOTE_BOOK_MAX_MB` 以下なら自動転送し、超過時は停止 |
| `SHOGIARENA_REMOTE_BOOK_TRANSFER` | `always` | サイズに関わらず転送する |
| `SHOGIARENA_REMOTE_BOOK_TRANSFER` | `preplaced` | 配置済みファイルの存在とSHA-256を実行前に検証する |
| `SHOGIARENA_REMOTE_BOOK_MAX_MB` | 整数 | `auto` の上限 MiB。既定は `256` |
| `SHOGIARENA_REMOTE_BOOK_PREPLACED_PATH` | POSIXの絶対パス | `preplaced`で必須のリモートのファイルパス |
| `SHOGIARENA_REMOTE_BOOK_PREPLACED_SHA256` | 小文字のSHA-256 | `preplaced`で必須の期待するハッシュ値 |
| `SHOGIARENA_REMOTE_PREPLACED_RESOURCES` | JSONオブジェクト | `--provision preplaced`で必須。エンジンや評価関数のIDをリモートの絶対パスとSHA-256へ対応付ける |

配置済みファイルの一覧は、`worker-bundle preplaced-map`で作れます。
エンジンや評価関数のハッシュ値を計算し、そのまま環境変数へ設定できるJSONを出力します。

```powershell
$env:SHOGIARENA_REMOTE_PREPLACED_RESOURCES = shogiarena worker-bundle preplaced-map `
  --engine engine-a C:\artifacts\linux-x86_64\engine-a /opt/engines/engine-a `
  --engine engine-b C:\artifacts\linux-x86_64\engine-b /opt/engines/engine-b `
  --resource engine-a C:\eval\engine-a /opt/eval/engine-a
```

`LOCAL_PATH`には、リモートで使うLinux x86_64の実行ファイルをローカルにコピーして指定します。
ShogiArenaをWindowsで操作する場合も、ここにはLinux用の実行ファイルを指定してください。
`REMOTE_PATH`へ配置したファイルと同じ内容である必要があります。

`--engine NAME LOCAL_PATH REMOTE_PATH`と`--resource ENGINE_NAME LOCAL_PATH REMOTE_PATH`は
必要な数だけ繰り返せます。
ディレクトリも指定できます。
実行時と同じ方法で内容を検証します。

`preplaced`では、両方のエンジンと必要な評価関数などをすべて一覧に含めてください。
配置済みの定跡を使う場合は、表にある定跡用の環境変数も指定します。
ローカルの内容、指定したSHA-256、リモートの内容が一致した場合に実行します。
ディレクトリを使う場合は、リモート側のディレクトリ権限を`0755`、ファイル権限を`0644`に揃える必要があります。

## リモート用の実行環境

リモートでは、ShogiArenaのwheel、依存関係、設定をまとめたworker bundleを使います。

```bash
shogiarena worker-bundle build --output worker-bundle.zip
```

対局ごとに独立した作業ディレクトリを使い、配置済みの実行環境は上書きしません。
再開時には保存したbundleを検証します。
保存された`remote-worker-bundle.zip`を復元できない場合は、新しい保存先で実行してください。

## 負荷分散

### スロット数の設定

`slots` は、各インスタンスで同時に使える実行容量です。

ShogiArenaは、エンジンの`Threads` / `USI_Threads`と`Ponder` / `USI_Ponder`から必要なスロット数を見積もります。
`tournament.num_parallel`で指定した並列対局が、`slots`と`max_engines`に収まるかを開始前に検査します。
`slots: null`では、開始前に取得したCPU数を使います。
CPU数を取得できない場合は停止します。
算出方法は [トーナメント](tournaments.md#並列数とインスタンス容量) を参照してください。

### エンジン数の制限

`max_engines` で、同時起動できるエンジンプロセス数を制限します。
省略すると、スロット数を上限として使います。
容量が一時的に埋まっている場合は`allocation_timeout`まで待ち、確保できなければ停止します。

## 参考資料

- [トーナメントガイド](tournaments.md)：トーナメント設定の詳細
- [エンジン設定](engine-configuration.md)：エンジン設定ファイルの書き方
