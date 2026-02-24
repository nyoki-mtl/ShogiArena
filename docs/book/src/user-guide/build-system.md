# ビルドシステムとアーティファクト参照

ShogiArena では、エンジンのソースコードリポジトリを登録し、特定のコミットをビルドして使用できます。

## アーティファクトとは

**アーティファクト**は、特定のコミット・ビルドオプションでビルドされたエンジンバイナリを指します。

```
artifact: YaneuraOu/a5ee2786
```

この形式により、以下が可能になります：
- バージョン管理されたエンジンの使用
- 再現可能なトーナメント実行
- CI/CD での自動ビルド

## 基本的な使い方

### 1. リポジトリの登録

まず、エンジンのリポジトリを ShogiArena に登録します。

```bash
shogiarena config repo set yaneuraou \
  --url https://github.com/yaneurao/YaneuraOu.git \
  --path ~/repos/YaneuraOu \
  --build-config ~/.config/shogiarena/builds/yaneuraou.yaml
```

| オプション | 説明 |
| --- | --- |
| `--url` | Git リポジトリの URL |
| `--path` | ローカルのクローン先ディレクトリ |
| `--build-config` | ビルド設定ファイルのパス |

### 2. ビルド設定ファイルの作成

ビルド設定ファイルには、ビルドの作業ディレクトリ・実行コマンド・成果物（バイナリ）の位置を記述します。

**~/.config/shogiarena/builds/yaneuraou.yaml**（例）:
```yaml
work_dir: "{repo.path}/source"
defaults:
  jobs: 4
commands:
  - ["make", "clean"]
  - ["make", "-j{opts.jobs}", "tournament"]
artifacts:
  - path: "{work_dir}/YaneuraOu-by-gcc"
    chmod: "755"
```

#### フィールドの説明

| フィールド | 説明 |
| --- | --- |
| `work_dir` | ビルドの作業ディレクトリ |
| `defaults` | `build_options` のデフォルト値 |
| `commands` | 実行するコマンド配列 |
| `artifacts[].path` | 生成されたバイナリへのパス |

### 3. トーナメント設定でアーティファクトを使用

```yaml
experiment_name: "artifact_tournament"

engines:
  - name: "YaneuraOu-main"
    artifact: "YaneuraOu/a5ee2786"
    options:
      Threads: 4
      Hash: 256
  
  - name: "YaneuraOu-other"
    artifact: "YaneuraOu/eb2856f9"
    options:
      Threads: 4
      Hash: 256

tournament:
  scheduler: round_robin
  games_per_pair: 100
```

## アーティファクト参照の形式

```
artifact: <repo_name>/<commit_hash>
```

- **repo_name**: `shogiarena config repo set` で登録した名前
- **commit_hash**: コミットハッシュ（6〜40桁）

### 例

```yaml
# 特定のコミットハッシュ
artifact: "YaneuraOu/a5ee2786"
```

## ビルドオプション

### CPU アーキテクチャの指定（任意）

```yaml
engines:
  - name: "YaneuraOu-ZEN3"
    artifact: "YaneuraOu/a5ee2786"
    build_options:
      target_cpu: "ZEN3"
    options:
      Threads: 16
      Hash: 8192
```

`build_options` は **ビルド設定が参照する場合のみ**必要です。`target_cpu` などは
ビルド設定内で `"{opts.target_cpu}"` を使っているときに限り指定します。

例: `target_cpu` を環境変数 `TARGET_CPU` として渡す場合

```bash
# 実行されるコマンド
TARGET_CPU=ZEN3 make -j4 TARGET=YaneuraOu-by-gcc
```

### カスタムビルドオプション（任意）

```yaml
engines:
  - name: "YaneuraOu-Custom"
    artifact: "YaneuraOu/a5ee2786"
    build_options:
      target_cpu: "AVX2"
      compiler: "gcc-12"
      extra_cflags: "-O3 -march=native"
```

これらはビルドコマンドで環境変数として使用できます（ビルド設定側で参照している場合のみ）。

**build config**:
```yaml
work_dir: "{repo.path}/source"
defaults:
  compiler: "gcc"
commands:
  - ["make", "clean"]
  - ["bash", "-lc", "COMPILER=${COMPILER} CFLAGS='${EXTRA_CFLAGS}' make -j4 tournament"]
artifacts:
  - path: "{work_dir}/YaneuraOu-by-gcc"
    chmod: "755"
```

## FukauraOu (DLShogi互換) の注意点

FukauraOu は **YaneuraOu と同一リポジトリ**のビルドオプション違いです。  
artifact 名は `FukauraOu/<commit>` を使い、ビルド設定は `builds/fukauraou.yaml` を利用します。

**~/.config/shogiarena/builds/fukauraou.yaml**（例）:
```yaml
work_dir: "{repo.path}/source"
env:
  CUDA_HOME: /usr/local/cuda-12.9
  PATH: /usr/local/cuda-12.9/bin:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin
defaults:
  compiler: clang++
  target: tournament
  jobs: 4
  edition: YANEURAOU_ENGINE_DEEP_TENSOR_RT_UBUNTU
commands:
  - ["make", "clean"]
  - ["make", "-j{opts.jobs}", "{opts.target}",
     "TARGET_CPU={opts.target_cpu}",
     "COMPILER={opts.compiler}",
     "YANEURAOU_EDITION={opts.edition}",
     "EXTRA_CPPFLAGS=-I/usr/local/cuda-12.9/include",
     "EXTRA_LDFLAGS=-L/usr/local/cuda-12.9/lib64",
     "EXTRA_LDFLAGS+=-L/usr/local/cuda-12.9/lib64/stubs"]
artifacts:
  - path: "{work_dir}/YaneuraOu-by-gcc"
    chmod: "755"
```

### 推奨 build_options

- `target_cpu`: 例 `AVX2`（必須）
- `edition`: 既定は `YANEURAOU_ENGINE_DEEP_TENSOR_RT_UBUNTU`
- `compiler`, `target`, `jobs`: 必要に応じて上書き

実行時はUSIオプションで評価関数を指定します（例: `DNN_Model`, `EvalDir`）。

## DeepLearningShogi の注意点

DeepLearningShogi は CUDA/TensorRT を使うため、ビルド時に `nvcc` が見える必要があります。
devcontainer や CI では PATH が通っていないことがあるため、**build_config の `env` に CUDA を明示**してください。

**~/.config/shogiarena/builds/deeplearningshogi.yaml**（例）:
```yaml
work_dir: "{repo.path}/usi"
env:
  CUDA_HOME: /usr/local/cuda
  PATH: /usr/local/cuda/bin:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin
defaults:
  jobs: 4
commands:
  - ["make", "clean"]
  - ["make", "-j{opts.jobs}"]
artifacts:
  - path: "{work_dir}/bin/usi"
    chmod: "755"
```

実行時は USI オプションで評価関数を指定します（例: `DNN_Model`）。

## プライベートリポジトリの使用

プライベートリポジトリにアクセスする場合、GitHub Personal Access Token (PAT) が必要です。

### 1. トークンの作成

GitHub の **Settings** → **Developer settings** → **Personal access tokens** → **Tokens (classic)**

必要な権限: **Contents: Read-only**

### 2. トークンの設定

```bash
shogiarena config init --github-token ghp_xxxxxxxxxxxx
```

または `settings.yaml` を直接編集：

```yaml
github_token: ghp_xxxxxxxxxxxx
```

### 3. プライベートリポジトリの登録

```bash
shogiarena config repo set myengine \
  --url https://github.com/myorg/myengine.git \
  --path ~/repos/myengine \
  --build-config ~/.config/shogiarena/builds/myengine.yaml
```

トークンは自動的に使用されます。

## キャッシュとビルドの管理

### ビルド済みバイナリのキャッシュ

ビルド済みのバイナリは `{engine_dir}` 以下にキャッシュされます。

```
{engine_dir}/
└── artifacts/
    └── YaneuraOu/
        ├── main/
        │   └── YaneuraOu-by-gcc/
        │       └── YaneuraOu-by-gcc
        └── abc123def/
            └── YaneuraOu-by-gcc/
                └── YaneuraOu-by-gcc
```

### キャッシュのクリア

```bash
rm -rf {engine_dir}/artifacts/yaneuraou
```

### 強制再ビルド

現在のところ、強制再ビルドのフラグはありません。
キャッシュを削除することで再ビルドできます。

## Git ワークツリーの管理

### Git の状態チェック

デフォルトでは、ビルド前に Git ワークツリーの状態がチェックされます。

| モード | 説明 |
| --- | --- |
| `strict` | クリーンな状態のみ許可（デフォルト） |
| `clean` | コミット済みであれば OK（未追跡ファイルは無視） |
| `allow-dirty` | 変更があってもビルド |

```bash
shogiarena run tournament tournament.yaml \
  --git-worktree allow-dirty
```

### ワークツリーの確認

```bash
cd ~/repos/YaneuraOu
git status
```

## ビルド設定の例

### YaneuraOu

**~/.config/shogiarena/builds/yaneuraou.yaml**:
```yaml
commits:
  main:
    targets:
      YaneuraOu-by-gcc:
        command: "make -j$(nproc) TARGET=YaneuraOu-by-gcc"
        binary: "YaneuraOu-by-gcc"
      
      YaneuraOu-by-clang:
        command: "make -j$(nproc) TARGET=YaneuraOu-by-clang"
        binary: "YaneuraOu-by-clang"
      
      YaneuraOu-avx2:
        command: "TARGET_CPU=AVX2 make -j$(nproc) TARGET=YaneuraOu-by-gcc"
        binary: "YaneuraOu-by-gcc"
```

### Makefile がないエンジン

```yaml
commits:
  main:
    targets:
      myengine:
        command: |
          mkdir -p build
          cd build
          cmake .. -DCMAKE_BUILD_TYPE=Release
          make -j$(nproc)
        binary: "build/myengine"
```

### Rust プロジェクト

```yaml
commits:
  main:
    targets:
      release:
        command: "cargo build --release"
        binary: "target/release/myengine"
      
      native:
        command: "RUSTFLAGS='-C target-cpu=native' cargo build --release"
        binary: "target/release/myengine"
```

## トラブルシューティング

### ビルドエラー

#### コマンドが失敗する

**解決**:
1. ビルドコマンドを手動で実行してエラーを確認
   ```bash
   cd ~/repos/YaneuraOu
   make -j4 TARGET=YaneuraOu-by-gcc
   ```

2. 依存パッケージをインストール
   ```bash
   # Ubuntu
   sudo apt install build-essential
   
   # macOS
   xcode-select --install
   ```

3. ビルド設定を修正
   ```yaml
   command: "make clean && make -j4 TARGET=YaneuraOu-by-gcc"
   ```

#### バイナリが見つからない

**エラー**: `Binary not found: YaneuraOu-by-gcc`

**解決**:
1. ビルド後のファイルを確認
   ```bash
   ls -l ~/repos/YaneuraOu/
   ```

2. `binary` フィールドを修正
   ```yaml
   binary: "path/to/actual/binary"
   ```

### リポジトリのクローンエラー

#### 認証エラー

**エラー**: `Authentication failed`

**解決**:
1. GitHub トークンを設定
   ```bash
   shogiarena config init --github-token ghp_xxxxxxxxxxxx
   ```

2. トークンの権限を確認（Contents: Read が必要）

#### ネットワークエラー

**解決**:
1. 手動でクローン
   ```bash
   git clone https://github.com/yaneurao/YaneuraOu.git ~/repos/YaneuraOu
   ```

2. リポジトリを登録
   ```bash
   shogiarena config repo set yaneuraou \
     --path ~/repos/YaneuraOu \
     --build-config builds/yaneuraou.yaml
   ```

## 参考資料

- [設定システム](configuration.md): リポジトリ登録の詳細
- [エンジン設定](engine-configuration.md): `engine_path` と `artifact` の使い分け
- [トーナメントガイド](tournaments.md): アーティファクトを使用したトーナメント
