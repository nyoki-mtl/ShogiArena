# エンジン設定ファイル

ShogiArena で使用するエンジンの設定方法を詳しく解説します。

## 基本構造

エンジン設定ファイルは YAML 形式で記述します。

```yaml
name: "MyEngine"
engine_path: "/path/to/engine/binary"
options:
  Threads: 4
  Hash: 256
```

## 必須フィールド

### name

エンジンの表示名です。ダッシュボードやログで使用されます。

```yaml
name: "YaneuraOu-Strong"
```

### engine_path

エンジンのバイナリファイルへのパスです。

```yaml
# 絶対パス
engine_path: "/home/user/engines/YaneuraOu"

# プレースホルダーを使用（config init 済みの場合）
engine_path: "{engine_dir}/yaneuraou/YaneuraOu"

# 相対パス（設定ファイルからの相対パス）
engine_path: "../../bin/myengine"
```

> **Tip: プレースホルダー**
>
> `{engine_dir}` は `shogiarena config init` で設定したエンジンディレクトリに展開されます。
> 複数の環境で設定を共有する場合に便利です。


## オプショナルフィールド

### options

USI エンジンのオプション設定です。`setoption name X value Y` として送信されます。

```yaml
options:
  Threads: 4
  Hash: 256
  BookFile: "standard_book.db"
  NetworkDelay: 120
  NetworkDelay2: 1120
  # エンジン固有のオプションも指定可能
  Contempt: 2
  ContemptFromBlack: false
```

> **Note: エンジン依存のオプション**
>
> 利用可能なオプションはエンジンによって異なります。
> エンジンのドキュメントを参照してください。


### working_dir

エンジンの作業ディレクトリを指定します。

```yaml
working_dir: "/path/to/engine/directory"
```

定義しない場合、エンジンバイナリと同じディレクトリが使用されます。

### env

エンジン実行時の環境変数を設定します。

```yaml
env:
  LD_LIBRARY_PATH: "/usr/local/lib"
  OMP_NUM_THREADS: "4"
```

### handshake_timeout

エンジンの起動時ハンドシェイクタイムアウト時間（秒、float）です。デフォルトは `None`（システムデフォルトを使用）です。

```yaml
handshake_timeout: 30.0
```

大規模なニューラルネットワークモデルを読み込むエンジンでは、長めに設定する必要があります。

### mate_default_ply_limit / mate_default_node_limit / mate_default_infinite

`think_mate()` で個別指定がないときの `go mate` 送信形式を指定します。

```yaml
mate_default_ply_limit: 30000
# または
mate_default_node_limit: 2000000
# または
mate_default_infinite: true
```

`mate_default_ply_limit` と `mate_default_node_limit` は同時指定できません。`mate_default_infinite` は両者と排他です。

### mate_wait_for_bestmove

`checkmate` を受けたあと、trailing `bestmove` を待ってから `think_mate()` を完了させる既定値です。

```yaml
mate_wait_for_bestmove: true
```

### isready_sync_strategy

`trigger_isready` 実行時に、進行中の探索とどう同期するかを指定します。

```yaml
isready_sync_strategy: "direct"  # direct | wait | stop
```

- `direct`: そのまま `isready` を送信
- `wait`: 進行中探索の完了を待ってから `isready`
- `stop`: `stop` を送って探索停止後に `isready`

## 使用例

### 基本的な設定

```yaml
name: "YaneuraOu"
engine_path: "/usr/local/bin/YaneuraOu"
options:
  Threads: 2
  Hash: 128
```

### 強さ調整したエンジン

```yaml
name: "YaneuraOu-Weak"
engine_path: "/usr/local/bin/YaneuraOu"
options:
  Threads: 1
  Hash: 64
  SkillLevel: 5  # エンジンがサポートしている場合
```

### ニューラルネットワークエンジン

```yaml
name: "DLShogi"
engine_path: "/opt/dlshogi/bin/dlshogi"
working_dir: "/opt/dlshogi"
handshake_timeout: 60.0  # モデル読み込みに時間がかかる
options:
  model_path: "model/model.onnx"
  use_gpu: true
  gpu_id: 0
env:
  CUDA_VISIBLE_DEVICES: "0"
```

### プレースホルダーを使用

```yaml
name: "YaneuraOu-ZEN3"
engine_path: "{engine_dir}/YaneuraOu/YaneuraOu-by-gcc-zen3"
options:
  Threads: 16
  Hash: 8192
  BookFile: "{engine_dir}/books/standard.db"
```

## トーナメント設定での上書き

トーナメント設定ファイル内で、エンジン設定の一部を上書きできます。

```yaml
# tournament.yaml
engines:
  - engine_path: "configs/engine/yaneuraou.yaml"
    name: "YaneuraOu-Strong"
    options:
      Threads: 8      # engine_path の値を上書き
      Hash: 1024
  
  - engine_path: "configs/engine/yaneuraou.yaml"
    name: "YaneuraOu-Weak"
    options:
      Threads: 1
      Hash: 128
```

この機能を使うと、同じエンジンバイナリで強さを変えたバリエーションを簡単に作成できます。

## アーティファクト参照

ビルド済みエンジンを artifact として参照することもできます。詳細は[ビルドシステムガイド](build-system.md)を参照してください。

```yaml
# engine_path の代わりに artifact を指定
artifact: "YaneuraOu/a5ee2786"
# build_options はビルド設定で参照される場合のみ指定
```

### DeepLearningShogi の例

```yaml
artifact: "DeepLearningShogi/ef2306c"
options:
  DNN_Model: "/path/to/model.onnx"
  Draw_Ply: 320
```

- `DNN_Model` は評価関数（ONNX）を指定します。
- `Draw_Ply` は引き分け手数の USI オプション名です。

### FukauraOu (DLShogi互換) の例

```yaml
artifact: "FukauraOu/743186e7"
build_options:
  target_cpu: "AVX2"
options:
  EvalDir: "eval"
  DNN_Model: "model.onnx"
```

- FukauraOu は YaneuraOu と同一repoのビルドオプション違いです。
- `EvalDir` は engine 実行ディレクトリからの相対パスで解決されます。

## 設定の検証

エンジン設定が正しいか確認するには、以下のコマンドを使用します。

```bash
# エンジンが起動するか確認
shogiarena run mate configs/engine/myengine.yaml startpos --ply-limit 5

# トーナメント設定を dry-run で検証
shogiarena run tournament configs/arena/test.yaml --dry-run
```

## トラブルシューティング

### エンジンが起動しない

1. **パスが正しいか確認**
   ```bash
   ls -l /path/to/engine/binary
   ```

2. **実行権限があるか確認**
   ```bash
   chmod +x /path/to/engine/binary
   ```

3. **依存ライブラリが揃っているか確認**
   ```bash
   ldd /path/to/engine/binary
   ```

### タイムアウトエラー

ニューラルネットワークモデルの読み込みに時間がかかるエンジンでは、`handshake_timeout` を長めに設定してください。

```yaml
handshake_timeout: 60.0
```

### オプションが反映されない

エンジンがサポートしているオプション名を確認してください。大文字小文字やスペースの有無に注意が必要です。

```bash
# エンジンのオプション一覧を確認（手動で起動）
/path/to/engine/binary
> usi
> quit
```

## 参考資料

- [トーナメントガイド](tournaments.md): トーナメント設定での上書き
- [ビルドシステム](build-system.md): アーティファクト参照の詳細
- [USI プロトコル](../technical/usi-engine.md): USI の技術詳細
